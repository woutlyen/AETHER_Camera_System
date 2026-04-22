import subprocess
import time
import signal
import sys
import json
import os

fallback_active = False
fallback_start_time = None
fallback_period_expired = False
FALLBACK_DURATION = 30 * 60  # 30 minutes

running = {}

def load_config(filename="/home/rpi/Camera/config.json"):
    with open(filename, "r") as f:
        return json.load(f)


def graceful_shutdown(sig, frame):
    print("Shutting down all processes gracefully...")
    for name, proc in running.items():
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)


while True:
    config = load_config()

    hw = config.get("system", {}).get("hardware_status", {})

    can_available = hw.get("can", False)
    if not can_available:
        if not fallback_active and not fallback_period_expired:
            print("CAN not available -> entering fallback mode")
            fallback_active = True
            fallback_start_time = time.time()
            
    if fallback_active:
        elapsed = time.time() - fallback_start_time

        if elapsed > FALLBACK_DURATION:
            print("Fallback duration expired -> stopping cameras")
            fallback_active = False
            fallback_period_expired = True

    env_vars = os.environ.copy()
    env_vars["LOGGING_ENABLED"] = str(config["features"]["logging"]).lower()

    desired_processes = {}

    # CAN
    if can_available:
        desired_processes["can_listener"] = [
            "/usr/bin/python3",
            "/home/rpi/Camera/can_listener.py"
        ]

    # Cameras
    for cam in config["cameras"]:
        name = cam["name"]

        if fallback_active:
            enabled = True  # FORCE ENABLE
        else:
            enabled = cam["enabled"] and hw.get(name, False)

        if not enabled:
            continue

        use_sd = hw.get("sd_card", False)

        desired_processes[name] = [
            "/usr/bin/python3",
            "/home/rpi/Camera/camera.py",
            cam["camera_path"],
            str(cam["stream_index"]),
            str(cam["udp_port"]),
            name,
            str(use_sd) 
        ]

    # udp_mjpeg
    for udp_mjpeg in config["udp_mjpegs"]:
        if udp_mjpeg["enabled"]:
            name = udp_mjpeg["name"]
            desired_processes[name] = [
                "/usr/bin/python3",
                "/home/rpi/Camera/udp_mjpeg.py",
                str(udp_mjpeg["udp_port_in"]),
                udp_mjpeg["udp_address_out"],
                str(udp_mjpeg["udp_port_out"]),
                name
            ]

    # SPI
    if fallback_active:
        spi_enabled = True
    else:
        spi_enabled = config["spi"]["enabled"]

    if spi_enabled:
        udp_ports = ",".join(str(cam["udp_port"]) for cam in config["cameras"]) # if cam["enabled"])

        desired_processes["spi"] = [
            "/usr/bin/python3",
            "/home/rpi/Camera/spi_mux.py",
            str(config["spi"]["bus"]),
            str(config["spi"]["device"]),
            str(config["spi"]["speed"]),
            udp_ports
        ]

    # Start / Stop logic
    for name, cmd in desired_processes.items():
        if name not in running or running[name].poll() is not None:
            print(f"Starting {name}...")
            running[name] = subprocess.Popen(cmd, env=env_vars)

    for name in list(running.keys()):
        if name not in desired_processes:
            print(f"Stopping {name}...")
            running[name].terminate()
            running[name].wait()
            del running[name]

    time.sleep(1)