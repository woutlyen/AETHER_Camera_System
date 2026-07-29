"""
Shared SD card detection utilities.

The SD card reader always exposes the card as /dev/mmcblk2 (or a partition of
it, e.g. mmcblk2p1), regardless of which physical card is inserted, while its
auto-mounted path (/media/pi/<label>) changes per card. These helpers resolve
the mount point dynamically via /proc/mounts so the system works with any
card. Shared between health_check.py (boot-time check) and supervisor.py
(runtime hot-swap monitoring) so both stay in sync.
"""

import os

SD_CARD_DEVICE_NAME = "mmcblk2"


def find_sd_card_mount_point():
    """
    Locate the current mount point of the SD card reader by device name.

    Returns:
        Mount point path, or None if the device isn't currently mounted
    """
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                device, mount_point = parts[0], parts[1]
                if os.path.basename(device).startswith(SD_CARD_DEVICE_NAME):
                    # /proc/mounts escapes spaces etc. as octal (e.g. \040)
                    return mount_point.encode().decode("unicode_escape")
    except Exception:
        pass

    return None


def check_sd_card_available():
    """
    Check if the SD card is currently plugged in, mounted, and writable.

    Returns:
        Tuple of (is_available, mount_point)
    """
    mount_point = find_sd_card_mount_point()
    if not mount_point:
        return (False, None)

    try:
        if not os.path.isdir(mount_point) or not os.access(mount_point, os.R_OK | os.W_OK):
            return (False, None)

        # camera.py writes into <mount_point>/streams/ - make sure it exists
        # so any freshly inserted card works without manual setup
        os.makedirs(os.path.join(mount_point, "streams"), exist_ok=True)
    except Exception:
        return (False, None)

    return (True, mount_point)
