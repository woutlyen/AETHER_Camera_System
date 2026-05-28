"""
CAN Message Definitions and Constants for AETHER Camera System

All CAN messages use Big-Endian byte ordering. This module defines all message IDs,
response codes, and utility functions for CAN communication.
"""

# Response Codes
CS_REP_OK = 0xFF    # Indicates successful operation
CS_REP_NOK = 0x00   # Indicates failed operation

# ============================================================================
# HEALTH CHECK MESSAGES (Transmitted by camera system on startup)
# ============================================================================

CS_WAKE_UP_ID = 0x0BF
CS_WAKE_UP_REPLY_ID = 0x4BF
CS_WAKE_UP_TIMEOUT = 5  # seconds between retries
CS_WAKE_UP_MAX_ATTEMPTS = 10

# ============================================================================
# SUPERVISOR MESSAGES (Transmitted by supervisor)
# ============================================================================

# Telemetry message - transmitted every 1 second
CS_STATUS_ID = 0x505
CS_STATUS_LENGTH = 8  # bytes

# Power cycle command - transmitted on critical failure
CS_POWER_CYCLE_ID = 0x0BE

# ============================================================================
# CAN LISTENER COMMANDS (Received by camera system)
# ============================================================================

# General commands
CS_PING_ID = 0x490
CS_PING_REPLY_ID = 0x090

# Camera 1 control
CS_CAMERA1_ENABLE_ID = 0x491
CS_CAMERA1_ENABLE_REPLY_ID = 0x091

CS_CAMERA1_DISABLE_ID = 0x492
CS_CAMERA1_DISABLE_REPLY_ID = 0x092

# Camera 2 control
CS_CAMERA2_ENABLE_ID = 0x493
CS_CAMERA2_ENABLE_REPLY_ID = 0x093

CS_CAMERA2_DISABLE_ID = 0x494
CS_CAMERA2_DISABLE_REPLY_ID = 0x094

# SPI control
CS_SPI_ENABLE_ID = 0x495
CS_SPI_ENABLE_REPLY_ID = 0x095

CS_SPI_DISABLE_ID = 0x496
CS_SPI_DISABLE_REPLY_ID = 0x096

# ============================================================================
# CAN LISTENER COMMAND MAPPING
# Maps arbitration ID to (config_key_path, enable_value, reply_id)
# config_key_path: list of keys to traverse config dict (e.g. ["cameras", 0, "enabled"])
# ============================================================================

COMMAND_MAPPING = {
    CS_PING_ID: {
        "name": "CS_PING",
        "reply_id": CS_PING_REPLY_ID,
        "handler": "ping",  # Special handler, doesn't update config
    },
    CS_CAMERA1_ENABLE_ID: {
        "name": "CS_CAMERA1_ENABLE",
        "reply_id": CS_CAMERA1_ENABLE_REPLY_ID,
        "config_path": ["cameras", 0, "enabled"],
        "value": True,
    },
    CS_CAMERA1_DISABLE_ID: {
        "name": "CS_CAMERA1_DISABLE",
        "reply_id": CS_CAMERA1_DISABLE_REPLY_ID,
        "config_path": ["cameras", 0, "enabled"],
        "value": False,
    },
    CS_CAMERA2_ENABLE_ID: {
        "name": "CS_CAMERA2_ENABLE",
        "reply_id": CS_CAMERA2_ENABLE_REPLY_ID,
        "config_path": ["cameras", 1, "enabled"],
        "value": True,
    },
    CS_CAMERA2_DISABLE_ID: {
        "name": "CS_CAMERA2_DISABLE",
        "reply_id": CS_CAMERA2_DISABLE_REPLY_ID,
        "config_path": ["cameras", 1, "enabled"],
        "value": False,
    },
    CS_SPI_ENABLE_ID: {
        "name": "CS_SPI_ENABLE",
        "reply_id": CS_SPI_ENABLE_REPLY_ID,
        "config_path": ["spi", "enabled"],
        "value": True,
    },
    CS_SPI_DISABLE_ID: {
        "name": "CS_SPI_DISABLE",
        "reply_id": CS_SPI_DISABLE_REPLY_ID,
        "config_path": ["spi", "enabled"],
        "value": False,
    },
}

# ============================================================================
# STATUS BYTE BIT MAPPING (CS_STATUS message, byte 7)
# Bits 7-5 are reserved (X), bits 4-0 contain status
# ============================================================================

STATUS_BIT_CAM1 = 0       # bit 0: Camera 1 status
STATUS_BIT_CAM2 = 1       # bit 1: Camera 2 status
STATUS_BIT_SD = 2         # bit 2: SD card status
STATUS_BIT_SPI_MUX = 3    # bit 3: SPI multiplexer status
STATUS_BIT_CAN = 4        # bit 4: CAN status


def set_status_bit(status_byte, bit_pos, enabled):
    """Set or clear a bit in the status byte."""
    if enabled:
        status_byte |= (1 << bit_pos)
    else:
        status_byte &= ~(1 << bit_pos)
    return status_byte


def get_status_bit(status_byte, bit_pos):
    """Get the value of a bit in the status byte."""
    return bool((status_byte >> bit_pos) & 1)
