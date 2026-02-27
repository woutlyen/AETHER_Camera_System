import subprocess
import time
import signal
import sys

import json
import os

processes = {
    "cam1": ["/usr/bin/python3", "/home/pi/Camera/cam1.py"],
    "cam2": ["/usr/bin/python3", "/home/pi/Camera/cam2.py"],
    "spi":  ["/usr/bin/python3", "/home/pi/Camera/spi_mux.py"],
}

running = {}

DEFAULT_CONFIG = {
    "enable_cam1": False,
    "enable_cam2": False,
    "enable_spi": False,
    "features": {
        "logging": False
    }
}

def load_config(filename="/home/pi/Camera/config.json"):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return DEFAULT_CONFIG
        
def save_config(config, filename="/home/pi/Camera/config.json"):
    temp_filename = filename + ".tmp"
    
    with open(temp_filename, "w") as f:
        json.dump(config, f, indent=4)
        f.flush()
        os.fsync(f.fileno())  # Force write to disk

    os.replace(temp_filename, filename)  # Atomic rename

# Gracefully handle termination with CTRL + C
def graceful_shutdown(sig, frame):
    print("Shutting down all processes gracefully...")

    for name, proc in running.items():
        print(f"Terminating {name}...")
        proc.terminate()  # Send SIGTERM to each process
        try:
            proc.wait(timeout=10)  # Wait for process to exit within 10 seconds
        except subprocess.TimeoutExpired:
            print(f"{name} did not exit in time, killing forcefully.")
            proc.kill()  # If it doesn't exit, kill it

    print("All processes stopped gracefully.")
    sys.exit(0)  # Exit the supervisor

# Register SIGINT handler (CTRL + C)
signal.signal(signal.SIGINT, graceful_shutdown)

# Main loop to check process states and restart if necessary
while True:
    config = load_config()

    for name, cmd in processes.items():
        # Check if the process should be enabled based on the config
        enable_key = f"enable_{name}"
        
        # Only start the process if it is enabled in the config
        if config.get(enable_key, False):  
            # Start the process if it's not running or has exited
            if name not in running or running[name].poll() is not None:
                print(f"Starting {name}...")
                
                # Set the logging flag from the config
                env_vars = os.environ.copy()  # Copy current environment variables
                env_vars["LOGGING_ENABLED"] = str(config["features"]["logging"]).lower()  # Set the logging flag
                
                running[name] = subprocess.Popen(cmd, env=env_vars)  # Start with custom environment variables
        
        # If the process is disabled in the config, ensure it's stopped
        elif name in running:
            print(f"Stopping {name} (disabled in config)...")
            running[name].terminate()  # Stop the process if it's running
            try:
                running[name].wait(timeout=10)  # Wait for process to exit
            except subprocess.TimeoutExpired:
                print(f"{name} did not exit in time, killing forcefully.")
                running[name].kill()  # Force kill if not exiting
            del running[name]  # Remove from the running dictionary

    time.sleep(2)
