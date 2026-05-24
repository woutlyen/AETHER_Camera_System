import sys
import os
import gi
import signal

gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

if len(sys.argv) != 6:
    print("Usage: camera.py <camera_name> <stream_index> <udp_port> <label> <use_sd>")
    sys.exit(1)

camera_name = sys.argv[1]
stream_index = sys.argv[2]
udp_port = sys.argv[3]
label = sys.argv[4]
use_sd = sys.argv[5].lower() in ["1", "true", "yes"]

filename_eMMC = f'/home/pi/camera/streams/Stream_{stream_index}_eMMC'
filename_SD = f'/mnt/sd/streams/Stream_{stream_index}_SD'


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
capsfilter caps=video/x-raw,width=1920,height=1080,format=NV12,interlace-mode=progressive,framerate=24/1 !
tee name=t !
queue !
videorate !
video/x-raw,framerate=20/1 !
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


pipeline = Gst.parse_launch(pipeline_str)


# -------------------------
# Graceful shutdown
# -------------------------

def shutdown_pipeline(sig, frame):
    print(f"Shutting down {label} pipeline gracefully...")
    pipeline.set_state(Gst.State.NULL)
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_pipeline)


# -------------------------
# Start pipeline
# -------------------------

print(f"[{label}] Starting pipeline")
print(f"[{label}] eMMC file: {eMMC_filename}")

if use_sd:
    print(f"[{label}] SD file: {SD_filename}")
else:
    print(f"[{label}] SD disabled")


pipeline.set_state(Gst.State.PLAYING)

bus = pipeline.get_bus()
msg = bus.timed_pop_filtered(
    Gst.CLOCK_TIME_NONE,
    Gst.MessageType.ERROR | Gst.MessageType.EOS
)

pipeline.set_state(Gst.State.NULL)
sys.exit(1)