import subprocess
import time
import signal
import sys
import json
import os

running = {}

def load_config(filename="/home/pi/Camera/config.json"):
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

    env_vars = os.environ.copy()
    env_vars["LOGGING_ENABLED"] = str(config["features"]["logging"]).lower()

    desired_processes = {}

    # CAN
    desired_processes["can_listener"] = [
        "/usr/bin/python3",
        "/home/pi/Camera/can_listener.py"
    ]

    # Cameras
    for cam in config["cameras"]:
        if cam["enabled"]:
            name = cam["name"]
            desired_processes[name] = [
                "/usr/bin/python3",
                "/home/pi/Camera/camera.py",
                cam["camera_path"],
                str(cam["stream_index"]),
                str(cam["udp_port"]),
                name
            ]

    # SPI
    if config["spi"]["enabled"]:
        udp_ports = ",".join(str(cam["udp_port"]) for cam in config["cameras"]) # if cam["enabled"])

        desired_processes["spi"] = [
            "/usr/bin/python3",
            "/home/pi/Camera/spi_mux.py",
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