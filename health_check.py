import os
import time
import subprocess
import sys
import json
import logging

CONFIG_FILE = "/home/rpi/Camera/config.json"

def load_config():
    # Load the JSON config file
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(config):
    # Save the config back to the JSON file atomically
    tmp_path = CONFIG_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=4)
    os.replace(tmp_path, CONFIG_FILE)  # atomic write


def get_retry_count(cfg):
    # Get the current boot retry count from the config
    return cfg["system"]["boot_retry_count"]


def set_retry_count(cfg, count):
    # Update the boot retry count in the config
    cfg["system"]["boot_retry_count"] = count


def setup_logging(enabled):
    # Configure logging to file and console if enabled, otherwise suppress all logs
    if enabled:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler("/home/rpi/Camera/boot.log"),
                logging.StreamHandler()
            ]
        )
    else:
        logging.basicConfig(level=logging.CRITICAL)


def update_hardware_status(cfg, sd, can, cam1, cam2):
    # Update the hardware status in the config based on the checks
    cfg["system"]["hardware_status"] = {
        "sd_card": sd,
        "can": can,
        "cam1": cam1,
        "cam2": cam2
    }

def check_mnt_sd_card():
    # Check if SD card is mounted at /mnt/sd
    try:
        result = subprocess.run(["lsblk", "-o", "NAME,MOUNTPOINT"], capture_output=True, text=True)
        return "mmcblk2p1" in result.stdout and "/mnt/sd" in result.stdout
    except:
        return False
    

def check_write_sd_card():
    # Try to write a small file to the SD card to ensure it's writable
    path = "/home/rpi/Camera"
    test_file = os.path.join(path, "test.tmp")

    try:
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except Exception:
        return False


def check_can():
    try:
        # Check interface exists and is UP
        result = subprocess.run(["ip", "link", "show", "can0"], capture_output=True, text=True)

        if "UP" not in result.stdout:
            return False

        # Verify driver init via dmesg
        dmesg = subprocess.run(["dmesg"], capture_output=True, text=True).stdout
        return "MCP2515 successfully initialized" in dmesg

    except:
        return False


def check_camera(device, camera_name):
    # Quick check using rpicam-hello to list cameras and verify the expected device is present
    try:
        result = subprocess.run(
            ["rpicam-hello", "--list-cameras"],
            capture_output=True,
            text=True
        )
        return device in result.stdout
    except Exception:
        return False


def reboot():
    print("Rebooting system...")
    subprocess.run(["reboot"])


def main():
    cfg = load_config()
    setup_logging(cfg["features"].get("logging", False))

    retry_count = get_retry_count(cfg)
    max_retries = cfg["system"]["max_retries"]

    logging.info(f"Boot attempt: {retry_count+1}/{max_retries}")

    sd_ok = check_mnt_sd_card() and check_write_sd_card()
    can_ok = check_can()
    cam1_ok = check_camera("ov5647", "/base/soc/i2c0mux/i2c@1/ov5647@36")
    cam2_ok = check_camera("ov5647", "/base/soc/i2c0mux/i2c@0/ov5647@36")

    update_hardware_status(cfg, sd_ok, can_ok, cam1_ok, cam2_ok)

    if sd_ok and can_ok and cam1_ok and cam2_ok:
        logging.info("All hardware OK")
        set_retry_count(cfg, 0)
        save_config(cfg)
        sys.exit(0) # All good, continue booting normally

    logging.warning(
        f"Hardware failed: SD={sd_ok}, CAN={can_ok}, CAM1={cam1_ok}, CAM2={cam2_ok}"
    )

    retry_count += 1
    set_retry_count(cfg, retry_count)

    if retry_count >= max_retries:
        logging.error("Max retries reached. Running in degraded mode.")

        save_config(cfg)

        sys.exit(0)  # Don't fail boot anymore, continue with degraded functionality

    save_config(cfg)

    time.sleep(2)
    reboot()


if __name__ == "__main__":
    main()