"""
CAN Listener for AETHER Camera System

Listens for CAN control commands on the can0 interface and updates the system
configuration accordingly. Sends reply messages for all commands.
"""

import can
import json
import time
import os
import sys
import logging

from can_constants import (
    COMMAND_MAPPING,
    CS_REP_OK,
    CS_REP_NOK,
    CS_PING_ID,
)

logging_level = os.getenv("LOGGING_LEVEL", "INFO").upper()

# Configure logging
logging.basicConfig(
    level=getattr(logging, logging_level, logging.INFO),
    format="%(asctime)s [CAN LISTENER] [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def load_config(filename="config.json"):
    """Load configuration from JSON file."""
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return None


def save_config(config, filename="config.json"):
    """Save configuration to JSON file using atomic write."""
    try:
        tmp_path = filename + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(config, f, indent=4)
        os.replace(tmp_path, filename)  # atomic write
        logger.info(f"Config saved atomically")
    except Exception as e:
        logger.error(f"Failed to save config: {e}")


def set_config_value(config, key_path, value):
    """
    Set a value in the config dict following a key path.
    
    Args:
        config: Configuration dictionary
        key_path: List of keys to traverse (e.g., ["cameras", 0, "enabled"])
        value: Value to set
    
    Returns:
        True if successful, False otherwise
    """
    try:
        current = config
        for key in key_path[:-1]:
            current = current[key]
        current[key_path[-1]] = value
        return True
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Failed to set config value at {key_path}: {e}")
        return False


def get_config_value(config, key_path):
    """
    Get a value from the config dict following a key path.
    
    Args:
        config: Configuration dictionary
        key_path: List of keys to traverse
    
    Returns:
        Value if found, None otherwise
    """
    try:
        current = config
        for key in key_path:
            current = current[key]
        return current
    except (KeyError, IndexError, TypeError):
        return None


def handle_command(bus, arbitration_id, data):
    """
    Handle a received CAN command.
    
    Args:
        bus: CAN bus interface
        arbitration_id: Message arbitration ID
        data: Message data (single byte)
    
    Returns:
        True if command was handled successfully, False otherwise
    """
    if arbitration_id not in COMMAND_MAPPING:
        logger.warning(f"Unknown command ID: {hex(arbitration_id)}")
        return False
    
    cmd_info = COMMAND_MAPPING[arbitration_id]
    cmd_name = cmd_info["name"]
    reply_id = cmd_info["reply_id"]
    
    logger.info(f"Received command: {cmd_name} ({hex(arbitration_id)})")
    
    # Handle special commands
    if "handler" in cmd_info:
        if cmd_info["handler"] == "ping":
            # Just send OK reply for ping
            logger.info(f"Responding to PING with OK")
            send_reply(bus, reply_id, CS_REP_OK)
            return True
    
    # Handle config-updating commands
    if "config_path" in cmd_info:
        config = load_config()
        if config is None:
            logger.error(f"Failed to handle {cmd_name}: config load failed")
            send_reply(bus, reply_id, CS_REP_NOK)
            return False
        
        new_value = cmd_info["value"]
        
        if set_config_value(config, cmd_info["config_path"], new_value):
            save_config(config)
            logger.info(f"Updated config for {cmd_name}: {new_value}")
            send_reply(bus, reply_id, CS_REP_OK)
            return True
        else:
            logger.error(f"Failed to update config for {cmd_name}")
            send_reply(bus, reply_id, CS_REP_NOK)
            return False
    
    logger.warning(f"Command {cmd_name} has no handler")
    send_reply(bus, reply_id, CS_REP_NOK)
    return False


def send_reply(bus, reply_id, payload):
    """
    Send a reply message on the CAN bus.
    
    Args:
        bus: CAN bus interface
        reply_id: Reply message arbitration ID
        payload: Single byte payload (CS_REP_OK or CS_REP_NOK)
    """
    try:
        msg = can.Message(
            arbitration_id=reply_id,
            data=[payload],
            is_extended_id=False
        )
        bus.send(msg)
        logger.info(f"Sent reply: {hex(reply_id)} = {hex(payload)}")
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")


def main():
    """Main CAN listener loop."""
    try:
        # Create CAN bus with filter for command IDs
        # Filter accepts messages from 0x490 to 0x496
        bus = can.interface.Bus(
            interface='socketcan',
            channel='can0',
            can_filters=[{"can_id": 0x490, "can_mask": 0x7F8}],  # Matches 0x490-0x497
        )
        logger.info("CAN listener started on can0")
    except Exception as e:
        logger.error(f"Failed to initialize CAN bus: {e}")
        sys.exit(1)
    
    try:
        while True:
            msg = bus.recv(timeout=1.0)
            
            if msg is None:
                continue
            
            # Validate message: must have exactly 1 data byte
            if len(msg.data) != 1:
                logger.warning(f"Invalid message length: {len(msg.data)} bytes (expected 1)")
                continue
            
            # Process the command
            handle_command(bus, msg.arbitration_id, msg.data[0])
    
    except KeyboardInterrupt:
        logger.info("CAN listener shutting down...")
    except Exception as e:
        logger.error(f"CAN listener error: {e}")
        sys.exit(1)
    finally:
        bus.shutdown()
        logger.info("CAN bus closed")


if __name__ == "__main__":
    main()