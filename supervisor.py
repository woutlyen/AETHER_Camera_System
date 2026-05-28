"""
Camera System Supervisor

Manages the camera system processes based on configuration and hardware status.
Handles:
- Process lifecycle management
- Runtime health monitoring (data flow, RTP packets)
- SD card hot-swap detection and recovery
- Telemetry collection and transmission
- Critical failure detection
"""

import subprocess
import time
import signal
import sys
import json
import os
import psutil
import can
import logging

from can_constants import (
    CS_STATUS_ID,
    CS_STATUS_LENGTH,
    CS_POWER_CYCLE_ID,
    STATUS_BIT_CAM1,
    STATUS_BIT_CAM2,
    STATUS_BIT_SD,
    STATUS_BIT_SPI_MUX,
    STATUS_BIT_CAN,
    set_status_bit,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL STATE
# ============================================================================

fallback_active = False
fallback_start_time = None
fallback_period_expired = False
FALLBACK_DURATION = 30 * 60  # 30 minutes

running = {}  # {process_name: subprocess.Popen}
can_bus = None

# RTP packet monitoring via shared file
RTP_STATS_FILE = "/tmp/rtp_stats.json"
rtp_packet_rates = {"cam1": 0, "cam2": 0}  # Cached RTP rates from spi_mux

# Runtime health monitoring
health_check_interval = 5.0
last_health_check = time.time()

# File write monitoring (to detect if cameras are actually recording)
file_write_states = {}  # {camera_name: {"path": str, "last_size": int, "last_check": float}}
file_check_interval = 10.0
last_file_check = time.time()

# RTP packet monitoring (to detect if spi_mux is receiving data)
rtp_packet_states = {}  # {"spi": {"last_rates": (int, int), "last_check": float}}
rtp_check_interval = 10.0
last_rtp_check = time.time()

# SD card hot-swap detection
sd_state = {"available_at_boot": False, "currently_available": False, "last_check": time.time()}
SD_CHECK_INTERVAL = 5.0


# ============================================================================
# INITIALIZATION & SHUTDOWN
# ============================================================================

def load_config(filename="/home/pi/camera/config.json"):
    """Load configuration from JSON file."""
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return None


def graceful_shutdown(sig, frame):
    """Handle graceful shutdown on SIGINT/SIGTERM."""
    logger.info("Shutting down all processes gracefully...")
    for name, proc in running.items():
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception as e:
            logger.error(f"Error terminating {name}: {e}")
    
    if can_bus:
        try:
            can_bus.shutdown()
        except:
            pass
    
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


def initialize_can_bus():
    """Initialize CAN bus interface for telemetry transmission and command listening."""
    global can_bus
    try:
        can_bus = can.interface.Bus(
            interface='socketcan',
            channel='can0',
            can_filters=[]
        )
        logger.info("CAN bus initialized for telemetry transmission")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize CAN bus: {e}")
        return False


# ============================================================================
# SYSTEM METRICS
# ============================================================================

def get_cpu_usage():
    """Get CPU usage percentage."""
    try:
        return int(psutil.cpu_percent(interval=0.1))
    except:
        return 0


def get_cpu_temperature():
    """Get CPU temperature in Celsius."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_millidegrees = int(f.read().strip())
            return int(temp_millidegrees / 1000)
    except:
        return 0


def get_ram_usage():
    """Get RAM usage percentage."""
    try:
        return int(psutil.virtual_memory().percent)
    except:
        return 0


def get_storage_usage(path):
    """Get storage usage percentage for a given path."""
    try:
        return int(psutil.disk_usage(path).percent)
    except:
        return 0


def get_rtp_packet_rates():
    """
    Get RTP packet rates from the SPI multiplexer stats file.
    
    Returns:
        Tuple of (cam1_rate, cam2_rate) capped at 255
    """
    global rtp_packet_rates
    
    try:
        if os.path.exists(RTP_STATS_FILE):
            with open(RTP_STATS_FILE, "r") as f:
                stats = json.load(f)
                cam1_rate = min(stats.get("stream_1", 0), 255)
                cam2_rate = min(stats.get("stream_2", 0), 255)
                rtp_packet_rates = {"cam1": cam1_rate, "cam2": cam2_rate}
                return (cam1_rate, cam2_rate)
    except Exception as e:
        logger.debug(f"Failed to read RTP stats: {e}")
    
    return (rtp_packet_rates["cam1"], rtp_packet_rates["cam2"])


# ============================================================================
# SD CARD MANAGEMENT
# ============================================================================

def check_sd_card_available():
    """
    Check if SD card is mounted and accessible.
    Tries multiple common mount points.
    """
    possible_mount_points = [
        "/mnt/sd",
        "/mnt/sd0",
        "/media/sd",
        "/media/sd0",
        "/run/media/pi/sd",
        "/run/media/pi/sd0"
    ]
    
    for mount_point in possible_mount_points:
        try:
            if os.path.exists(mount_point):
                # Check if we can read/write
                if os.access(mount_point, os.R_OK | os.W_OK):
                    # Also check if the expected streams folder exists
                    streams_path = os.path.join(mount_point, "streams")
                    if os.path.exists(streams_path):
                        sd_state["mount_point"] = mount_point
                        return True
        except:
            pass
    
    return False


def update_sd_card_state():
    """
    Monitor SD card availability and detect hot-swap events.
    
    Returns:
        Tuple of (sd_now_available, just_reconnected, just_disconnected)
    """
    global sd_state
    
    current_time = time.time()
    if current_time - sd_state["last_check"] < SD_CHECK_INTERVAL:
        return (sd_state["currently_available"], False, False)
    
    sd_state["last_check"] = current_time
    was_available = sd_state["currently_available"]
    is_available = check_sd_card_available()
    sd_state["currently_available"] = is_available
    
    just_reconnected = not was_available and is_available
    just_disconnected = was_available and not is_available
    
    if just_reconnected:
        logger.info("SD card reconnected!")
    elif just_disconnected:
        logger.warning("SD card disconnected!")
    
    return (is_available, just_reconnected, just_disconnected)


def restart_camera_with_sd_config(camera_name, use_sd, config):
    """
    Stop and restart a camera pipeline with updated SD card configuration.
    
    Args:
        camera_name: Name of camera to restart
        use_sd: Whether to enable SD card output
        config: Full configuration dict
    """
    logger.info(f"Restarting {camera_name} with use_sd={use_sd}...")
    
    # Find the camera config
    cam_config = None
    for cam in config["cameras"]:
        if cam["name"] == camera_name:
            cam_config = cam
            break
    
    if not cam_config:
        logger.error(f"Camera config not found for {camera_name}")
        return
    
    # Stop the current process
    if camera_name in running:
        try:
            running[camera_name].terminate()
            running[camera_name].wait(timeout=10)
        except subprocess.TimeoutExpired:
            running[camera_name].kill()
        except Exception as e:
            logger.error(f"Error stopping {camera_name}: {e}")
        del running[camera_name]
    
    # Restart with new configuration
    env_vars = os.environ.copy()
    env_vars["LOGGING_ENABLED"] = str(config["features"]["logging"]).lower()
    
    cmd = [
        "/usr/bin/python3",
        "/home/pi/camera/camera.py",
        cam_config["camera_path"],
        str(cam_config["stream_index"]),
        str(cam_config["udp_port"]),
        camera_name,
        str(use_sd)
    ]
    
    try:
        running[camera_name] = subprocess.Popen(cmd, env=env_vars)
        logger.info(f"Restarted {camera_name} with use_sd={use_sd}")
    except Exception as e:
        logger.error(f"Failed to restart {camera_name}: {e}")


def handle_sd_card_hot_swap(config):
    """
    Handle SD card hot-swap events by restarting affected cameras.
    
    Args:
        config: Full configuration dict
    """
    sd_now_available, just_reconnected, just_disconnected = update_sd_card_state()
    
    if just_reconnected:
        # SD card reconnected - restart cameras to enable SD writing
        for cam in config["cameras"]:
            if cam["enabled"] and cam["name"] in running:
                restart_camera_with_sd_config(cam["name"], True, config)
    
    elif just_disconnected:
        # SD card disconnected - restart cameras to disable SD writing
        # This prevents crash if they try to write to unavailable SD
        for cam in config["cameras"]:
            if cam["enabled"] and cam["name"] in running:
                restart_camera_with_sd_config(cam["name"], False, config)


# ============================================================================
# HEALTH MONITORING & FAILURE DETECTION
# ============================================================================

def get_directory_size(directory_path):
    """
    Get total size of all files in a directory and subdirectories.
    
    Returns:
        Total size in bytes, or -1 if directory doesn't exist
    """
    try:
        if not os.path.exists(directory_path):
            return -1
        
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(directory_path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(filepath)
                except:
                    pass
        
        return total_size
    except:
        return -1


def check_camera_data_flow(config):
    """
    Verify that enabled cameras are actually writing data to storage directories.
    
    Monitors both eMMC (/home/pi/camera/streams/) and SD card (if available).
    Tracks directory size growth to detect stalls.
    
    Returns:
        Tuple of (all_healthy, unhealthy_cameras)
    """
    global file_write_states, last_file_check
    
    current_time = time.time()
    if current_time - last_file_check < file_check_interval:
        return (True, [])
    
    last_file_check = current_time
    unhealthy_cameras = []
    
    for cam in config["cameras"]:
        name = cam["name"]
        
        # Skip if camera is not enabled or not running
        if name not in running or running[name].poll() is not None:
            continue
        
        # Check eMMC storage (primary location)
        emmc_stream_dir = f"/home/pi/camera/streams/{name}"
        emmc_size = get_directory_size(emmc_stream_dir)
        
        # Check SD card storage (if available)
        sd_size = 0
        if sd_state["currently_available"] and sd_state.get("mount_point"):
            sd_stream_dir = f"{sd_state['mount_point']}/streams/{name}"
            sd_size = get_directory_size(sd_stream_dir)
        
        # Total size from both sources
        total_size = max(emmc_size, 0) + max(sd_size, 0)
        
        if total_size > 0:
            # Data exists - check if it's growing
            if name in file_write_states:
                prev_size = file_write_states[name]["last_size"]
                time_since_check = current_time - file_write_states[name]["last_check"]
                
                if total_size > prev_size:
                    # Data is growing - healthy
                    file_write_states[name] = {
                        "last_size": total_size,
                        "last_check": current_time
                    }
                else:
                    # Data not growing - potential stall
                    time_stalled = current_time - file_write_states[name]["last_check"]
                    if time_stalled > 30:  # 30 seconds without growth = stalled
                        unhealthy_cameras.append(name)
                        logger.error(f"Camera {name} data flow stalled for {time_stalled:.1f}s (size: {total_size} bytes)")
            else:
                # First check - just record the size
                file_write_states[name] = {
                    "last_size": total_size,
                    "last_check": current_time
                }
        else:
            # No data yet - check if giving enough time
            if name in file_write_states:
                time_since_start = current_time - file_write_states[name]["last_check"]
                if time_since_start > 60:  # 60 seconds and still no data = critical
                    unhealthy_cameras.append(name)
                    logger.error(f"Camera {name} not writing data for {time_since_start:.1f}s")
            else:
                # First check and no data yet - initialize and give time
                file_write_states[name] = {
                    "last_size": 0,
                    "last_check": current_time
                }
    
    return (len(unhealthy_cameras) == 0, unhealthy_cameras)


def check_spi_data_flow(config):
    """
    Verify that SPI multiplexer is receiving RTP packets if enabled.
    
    Returns:
        Tuple of (healthy, stalled_reason)
    """
    global rtp_packet_states, last_rtp_check
    
    current_time = time.time()
    if current_time - last_rtp_check < rtp_check_interval:
        return (True, "")
    
    last_rtp_check = current_time
    
    # Only check if SPI is enabled and running
    if "spi" not in running or running["spi"].poll() is not None:
        return (True, "")
    
    if not config["spi"]["enabled"]:
        return (True, "")
    
    # Check if we're receiving RTP packets
    cam1_rate, cam2_rate = get_rtp_packet_rates()
    
    # At least one camera should be sending if cameras are enabled
    cameras_enabled = any(cam["enabled"] for cam in config["cameras"])
    
    if cameras_enabled and cam1_rate == 0 and cam2_rate == 0:
        if "spi" in rtp_packet_states:
            prev_rates = rtp_packet_states["spi"].get("last_rates", (0, 0))
            if prev_rates == (0, 0):
                time_stalled = current_time - rtp_packet_states["spi"]["last_check"]
                if time_stalled > 30:  # 30 seconds without packets
                    reason = "SPI mux: No RTP packets received for 30+ seconds"
                    logger.error(reason)
                    return (False, reason)
        else:
            # First check without packets - give it time
            rtp_packet_states["spi"] = {
                "last_rates": (cam1_rate, cam2_rate),
                "last_check": current_time
            }
    else:
        # We're receiving packets or cameras disabled - reset stall counter
        rtp_packet_states["spi"] = {
            "last_rates": (cam1_rate, cam2_rate),
            "last_check": current_time
        }
    
    return (True, "")


def detect_critical_failures(config):
    """
    Detect actual critical failures requiring reboot (CS_POWER_CYCLE).
    
    Returns:
        Tuple of (should_reboot, failure_reason)
    """
    global last_health_check
    
    current_time = time.time()
    if current_time - last_health_check < health_check_interval:
        return (False, "")
    
    last_health_check = current_time
    
    # Check camera data flow
    data_flow_ok, stalled_cameras = check_camera_data_flow(config)
    if not data_flow_ok:
        reason = f"Critical: Camera data flow stalled for {stalled_cameras}"
        logger.error(reason)
        return (True, reason)
    
    # Check SPI data flow (only if SPI is enabled and cameras are enabled)
    if config["spi"]["enabled"] and any(cam["enabled"] for cam in config["cameras"]):
        spi_ok, spi_reason = check_spi_data_flow(config)
        if not spi_ok:
            logger.error(spi_reason)
            return (True, spi_reason)
    
    return (False, "")


# ============================================================================
# TELEMETRY & CAN COMMUNICATION
# ============================================================================

def collect_telemetry(config):
    """
    Collect system telemetry data for CS_STATUS message.
    Uses runtime state, not boot-time hardware_status.
    
    Returns:
        Dict with telemetry keys
    """
    # Get storage usage from correct paths
    emmc_usage = get_storage_usage("/home/pi/camera")  # eMMC mount
    
    # SD card usage (if available)
    sd_usage = 0
    if sd_state["currently_available"] and sd_state.get("mount_point"):
        sd_usage = get_storage_usage(sd_state["mount_point"])
    
    telemetry = {
        "cpu_usage": get_cpu_usage(),
        "cpu_temp": get_cpu_temperature(),
        "ram_usage": get_ram_usage(),
        "emmc_usage": emmc_usage,
        "sd_usage": sd_usage,
    }
    
    # Get RTP rates
    cam1_rtp, cam2_rtp = get_rtp_packet_rates()

    telemetry["cam1_rtp"] = cam1_rtp
    telemetry["cam2_rtp"] = cam2_rtp
    
    # Build status byte using runtime state (which processes are actually running)
    status_byte = 0
    status_byte = set_status_bit(status_byte, STATUS_BIT_CAM1, "cam1" in running and running["cam1"].poll() is None)
    status_byte = set_status_bit(status_byte, STATUS_BIT_CAM2, "cam2" in running and running["cam2"].poll() is None)
    status_byte = set_status_bit(status_byte, STATUS_BIT_SD, sd_state["currently_available"])
    status_byte = set_status_bit(status_byte, STATUS_BIT_SPI_MUX, "spi" in running and running["spi"].poll() is None)
    status_byte = set_status_bit(status_byte, STATUS_BIT_CAN, "can_listener" in running and running["can_listener"].poll() is None)
    
    telemetry["status_byte"] = status_byte
    
    return telemetry


def send_status_message(telemetry):
    """
    Send CS_STATUS telemetry message over CAN.
    
    Args:
        telemetry: Dict with telemetry data
    """
    if can_bus is None:
        return
    
    try:
        data = [
            telemetry["cpu_usage"],
            telemetry["cpu_temp"],
            telemetry["ram_usage"],
            telemetry["emmc_usage"],
            telemetry["sd_usage"],
            telemetry["cam1_rtp"],
            telemetry["cam2_rtp"],
            telemetry["status_byte"],
        ]
        
        msg = can.Message(
            arbitration_id=CS_STATUS_ID,
            data=data,
            is_extended_id=False
        )
        can_bus.send(msg)
        logger.debug(f"Sent CS_STATUS: {[hex(b) for b in data]}")
    except Exception as e:
        logger.error(f"Failed to send CS_STATUS: {e}")


def send_power_cycle_message(reason=""):
    """
    Send CS_POWER_CYCLE message to indicate critical failure.
    
    Args:
        reason: Description of the failure for logging
    """
    if can_bus is None:
        logger.warning(f"Cannot send CS_POWER_CYCLE: CAN bus unavailable. Reason: {reason}")
        return
    
    try:
        msg = can.Message(
            arbitration_id=CS_POWER_CYCLE_ID,
            data=[0x00],
            is_extended_id=False
        )
        can_bus.send(msg)
        logger.error(f"Sent CS_POWER_CYCLE due to: {reason}")
    except Exception as e:
        logger.error(f"Failed to send CS_POWER_CYCLE: {e}")


# ============================================================================
# PROCESS MANAGEMENT
# ============================================================================

def start_process(name, cmd, env_vars):
    """Start a subprocess with error handling."""
    try:
        running[name] = subprocess.Popen(cmd, env=env_vars)
        logger.info(f"Started {name}")
    except Exception as e:
        logger.error(f"Failed to start {name}: {e}")


def stop_process(name):
    """Stop a subprocess gracefully."""
    if name not in running:
        return
    
    try:
        running[name].terminate()
        running[name].wait(timeout=10)
    except subprocess.TimeoutExpired:
        running[name].kill()
    except Exception as e:
        logger.error(f"Error stopping {name}: {e}")
    finally:
        del running[name]


def manage_processes(config):
    """
    Determine which processes should be running based on config and fallback state.
    Start/stop processes as needed.
    
    Args:
        config: Full configuration dict
    """
    env_vars = os.environ.copy()
    env_vars["LOGGING_ENABLED"] = str(config["features"]["logging"]).lower()
    
    desired_processes = {}
    
    # ====================================================================
    # CAN LISTENER
    # ====================================================================
    
    hw = config.get("system", {}).get("hardware_status", {})
    can_available = hw.get("can", False)
    if can_available:
        desired_processes["can_listener"] = [
            "/usr/bin/python3",
            "/home/pi/camera/can_listener.py"
        ]
    
    # ====================================================================
    # CAMERAS
    # ====================================================================
    
    for cam in config["cameras"]:
        name = cam["name"]
        
        # In fallback mode, use hardware detection; otherwise use config
        if fallback_active:
            enabled = hw.get(name, False)
        else:
            enabled = cam["enabled"] and hw.get(name, False)
        
        if not enabled:
            continue
        
        use_sd = sd_state["currently_available"]
        sd_mount = sd_state.get("mount_point", "/mnt/sd") if use_sd else "/mnt/sd"
        
        desired_processes[name] = [
            "/usr/bin/python3",
            "/home/pi/camera/camera.py",
            cam["camera_path"],
            str(cam["stream_index"]),
            str(cam["udp_port"]),
            name,
            str(use_sd),
            sd_mount
        ]
    
    # ====================================================================
    # UDP MJPEG STREAMS
    # ====================================================================
    
    for udp_mjpeg in config["udp_mjpegs"]:
        if udp_mjpeg["enabled"]:
            name = udp_mjpeg["name"]
            desired_processes[name] = [
                "/usr/bin/python3",
                "/home/pi/camera/udp_mjpeg.py",
                str(udp_mjpeg["udp_port_in"]),
                udp_mjpeg["udp_address_out"],
                str(udp_mjpeg["udp_port_out"]),
                name
            ]
    
    # ====================================================================
    # SPI MULTIPLEXER
    # ====================================================================
    
    if fallback_active:
        spi_enabled = True
    else:
        spi_enabled = config["spi"]["enabled"]
    
    if spi_enabled:
        udp_ports = ",".join(str(cam["udp_port"]) for cam in config["cameras"])
        
        desired_processes["spi"] = [
            "/usr/bin/python3",
            "/home/pi/camera/spi_mux.py",
            str(config["spi"]["bus"]),
            str(config["spi"]["device"]),
            str(config["spi"]["speed"]),
            udp_ports
        ]
    
    # ====================================================================
    # START / STOP LOGIC
    # ====================================================================
    
    # Start desired processes
    for name, cmd in desired_processes.items():
        if name not in running or running[name].poll() is not None:
            logger.info(f"Starting {name}...")
            start_process(name, cmd, env_vars)
    
    # Stop undesired processes
    for name in list(running.keys()):
        if name not in desired_processes:
            logger.info(f"Stopping {name}...")
            stop_process(name)


# ============================================================================
# MAIN LOOP
# ============================================================================

def main():
    """Main supervisor loop."""
    global fallback_active, fallback_start_time, fallback_period_expired, sd_state
    
    # Initialize CAN bus
    initialize_can_bus()
    
    # Check SD card at startup
    sd_state["currently_available"] = check_sd_card_available()
    sd_state["available_at_boot"] = sd_state["currently_available"]
    logger.info(f"SD card available at boot: {sd_state['available_at_boot']}")
    
    logger.info("Camera system supervisor starting...")
    
    last_status_send = 0
    status_send_interval = 1.0
    
    try:
        while True:
            config = load_config()
            if config is None:
                logger.error("Failed to load config, retrying...")
                time.sleep(1)
                continue
            
            # ================================================================
            # CHECK FALLBACK MODE CONDITIONS
            # ================================================================
            
            hw = config.get("system", {}).get("hardware_status", {})
            can_available = hw.get("can", False)
            if not can_available:
                if not fallback_active and not fallback_period_expired:
                    logger.info("CAN not available → entering fallback mode")
                    fallback_active = True
                    fallback_start_time = time.time()
                
                if fallback_active:
                    elapsed = time.time() - fallback_start_time
                    if elapsed > FALLBACK_DURATION:
                        logger.info("Fallback duration expired → stopping cameras")
                        fallback_active = False
                        fallback_period_expired = True
            
            # ================================================================
            # SD CARD HOT-SWAP HANDLING
            # ================================================================
            
            # handle_sd_card_hot_swap(config)
            
            # ================================================================
            # PROCESS MANAGEMENT
            # ================================================================
            
            manage_processes(config)
            
            # ================================================================
            # TELEMETRY & STATUS
            # ================================================================
            
            current_time = time.time()
            if current_time - last_status_send >= status_send_interval:
                telemetry = collect_telemetry(config)
                send_status_message(telemetry)
                last_status_send = current_time
            
            # ================================================================
            # FAILURE DETECTION
            # ================================================================
            
            # should_reboot, failure_reason = detect_critical_failures(config)
            # if should_reboot:
            #     logger.error(f"Critical failure detected: {failure_reason}")
            #     send_power_cycle_message(failure_reason)
            #     # Note: actual reboot would be handled by external watchdog or systemd
            
            time.sleep(0.1)
    
    except Exception as e:
        logger.error(f"Supervisor error: {e}")
        send_power_cycle_message(f"Supervisor exception: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
