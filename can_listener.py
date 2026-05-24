import can
import json
import time
import os

CONFIG_PATH = "/home/pi/camera/config.json"

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(config):
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=4)
    os.replace(tmp_path, CONFIG_PATH)  # atomic write


def update_config(arbitration_id, value):
    config = load_config()

    enabled = bool(value)

    if arbitration_id == 0x500:
        config["cameras"][0]["enabled"] = enabled

    elif arbitration_id == 0x501:
        config["cameras"][1]["enabled"] = enabled

    elif arbitration_id == 0x502:
        config["spi"]["enabled"] = enabled

    else:
        return  # ignore other IDs

    save_config(config)
    print(f"Updated config from CAN: {hex(arbitration_id)} -> {enabled}")


def main():
    bus = can.interface.Bus(
        interface='socketcan', 
        channel='can0', 
        can_filters=[{"can_id": 0x500, "can_mask": 0x700},]
    )

    print("Listening on can0...")

    while True:
        msg = bus.recv()  # BLOCKING → no polling #TODO: Could raise an error, add fix!!!!!!!

        if msg is None or len(msg.data) > 1 or msg.data[0] > 1 :
            continue

        update_config(msg.arbitration_id, msg.data[0])


if __name__ == "__main__":
    main()