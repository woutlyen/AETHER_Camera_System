import sys
import os
import gi
import signal
import logging

gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

logging_level = os.getenv("LOGGING_LEVEL", "INFO").upper()

# Configure logging
logging.basicConfig(
    level=getattr(logging, logging_level, logging.INFO),
    format="%(asctime)s [CAMERA] [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

if len(sys.argv) < 6:
    logger.error(
        "Usage: camera.py <camera_name> <stream_index> <udp_port> "
        "<label> <use_sd> [sd_mount_point]"
    )
    sys.exit(1)

camera_name = sys.argv[1]
stream_index = sys.argv[2]
udp_port = sys.argv[3]
label = sys.argv[4]
use_sd = sys.argv[5].lower() in ["1", "true", "yes"]
sd_mount_point = sys.argv[6] if len(sys.argv) > 6 else "/mnt/sd"

filename_eMMC = f"/home/pi/camera/streams/camera{stream_index}"
filename_SD = f"{sd_mount_point}/streams/camera{stream_index}" if use_sd else None


def get_next_available_filename(base):
    index = 0

    while True:
        candidate = f"{base}_{index}.h264" if index > 0 else f"{base}.h264"

        if not os.path.exists(candidate) or os.path.getsize(candidate) == 0:
            return candidate

        index += 1


# Always generate eMMC filename
eMMC_filename = get_next_available_filename(filename_eMMC)

# Only generate SD filename if enabled
SD_filename = None
if use_sd:
    SD_filename = get_next_available_filename(filename_SD)


# -------------------------
# Build pipeline dynamically
# -------------------------

file_branch = f"""
v4l2h264enc extra-controls="controls,repeat_sequence_header=1" !
video/x-h264,level=(string)4 !
tee name=v !
queue !
filesink location={eMMC_filename}
"""

if use_sd:
    file_branch += f"""
v. ! queue ! filesink location={SD_filename}
"""


pipeline_str = f"""
libcamerasrc camera-name={camera_name} !
capsfilter caps=video/x-raw,width=1280,height=720,format=NV12,interlace-mode=progressive,framerate=30/1 !
tee name=t !
queue !
videorate !
video/x-raw,framerate=30/1 !
{file_branch}
t. ! queue !
videoscale !
video/x-raw,width=640,height=360 !
videorate !
video/x-raw,framerate=8/1 !
queue max-size-buffers=2 leaky=downstream !
x264enc tune=zerolatency bitrate=165 speed-preset=superfast key-int-max=8 intra-refresh=true bframes=0 aud=true option-string="slice-max-size=236" !
video/x-h264,stream-format=byte-stream,alignment=au !
h264parse config-interval=1 !
rtph264pay pt=96 mtu=242 config-interval=1 !
udpsink host=127.0.0.1 port={udp_port} sync=false async=false
"""

try:
    pipeline = Gst.parse_launch(pipeline_str)
except Exception as e:
    logger.error(f"Failed to create GStreamer pipeline: {e}")
    sys.exit(1)


# -------------------------
# Graceful shutdown
# -------------------------

def shutdown_pipeline(sig, frame):
    logger.info(f"Shutting down {label} pipeline...")
    pipeline.set_state(Gst.State.NULL)
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_pipeline)
signal.signal(signal.SIGTERM, shutdown_pipeline)


# -------------------------
# Start pipeline
# -------------------------

logger.info(f"Starting camera pipeline: {label}")
logger.info(f"Camera name: {camera_name}")
logger.info(f"UDP output port: {udp_port}")
logger.info(f"eMMC recording: {eMMC_filename}")

if use_sd:
    logger.info(f"SD recording: {SD_filename}")
else:
    logger.info("SD recording disabled")

ret = pipeline.set_state(Gst.State.PLAYING)

if ret == Gst.StateChangeReturn.FAILURE:
    logger.error("Failed to start GStreamer pipeline")
    sys.exit(1)

logger.info("Pipeline started successfully")

try:
    bus = pipeline.get_bus()

    while True:
        msg = bus.timed_pop_filtered(
            Gst.CLOCK_TIME_NONE,
            Gst.MessageType.ERROR
            | Gst.MessageType.EOS
            | Gst.MessageType.WARNING
        )

        if not msg:
            continue

        if msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            logger.error(f"GStreamer error: {err}")
            if debug:
                logger.debug(f"Debug info: {debug}")
            break

        elif msg.type == Gst.MessageType.WARNING:
            err, debug = msg.parse_warning()
            logger.warning(f"GStreamer warning: {err}")
            if debug:
                logger.debug(f"Debug info: {debug}")

        elif msg.type == Gst.MessageType.EOS:
            logger.info("Received EOS event")
            break

except KeyboardInterrupt:
    logger.info("Interrupted by user")

finally:
    logger.info("Stopping pipeline...")
    pipeline.set_state(Gst.State.NULL)
    logger.info("Camera pipeline shut down")
    sys.exit(0)