import sys
import os
import gi
import signal

gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

# Function to find the next available file name
def get_next_available_filename(base_path, base_filename):
    index = 0
    while True:
        # Create a candidate filename with the index (e.g., Stream_0_eMMC_1.h264)
        candidate_filename = f"{base_path}/{base_filename}_{index}.h264" if index > 0 else f"{base_path}/{base_filename}.h264"
        if not os.path.exists(candidate_filename) or os.path.getsize(candidate_filename) == 0:  # Check if the file exists
            return candidate_filename
        index += 1

# Set up paths for the output files
base_path = '/home/pi/Camera'
base_filename_eMMC = 'Stream_1_eMMC'
base_filename_SD = 'Stream_1_SD'

# Generate new file names to avoid overwriting
eMMC_filename = get_next_available_filename(base_path, base_filename_eMMC)
SD_filename = get_next_available_filename(base_path, base_filename_SD)

pipeline = Gst.parse_launch(f"""
libcamerasrc camera-name=/base/soc/i2c0mux/i2c@1/ov5647@36 !
capsfilter caps=video/x-raw,width=1920,height=1080,format=NV12,interlace-mode=progressive,framerate=24/1 !
tee name=t !
queue !
videorate !
video/x-raw,framerate=24/1 !
v4l2h264enc extra-controls="controls,repeat_sequence_header=1" !
video/x-h264,level=(string)4 !
tee name=v !
queue !
filesink location={eMMC_filename}
v. ! queue ! filesink location={SD_filename}
t. ! queue !
videoscale !
video/x-raw,width=640,height=360 !
videorate !
video/x-raw,framerate=8/1 !
queue max-size-buffers=2 leaky=downstream !
x264enc tune=zerolatency bitrate=200 speed-preset=superfast key-int-max=4 intra-refresh=true bframes=0 aud=true option-string="slice-max-size=200" !
video/x-h264,stream-format=byte-stream,alignment=au !
h264parse config-interval=1 !
rtph264pay pt=96 mtu=255 config-interval=1 !
udpsink host=127.0.0.1 port=6001 sync=false async=false
""")

# Graceful shutdown to stop GStreamer pipeline
def shutdown_pipeline(sig, frame):
    print("Shutting down cam2 pipeline gracefully...")
    pipeline.set_state(Gst.State.NULL)  # Stop the pipeline
    sys.exit(0)  # Exit the process

# Register SIGINT handler (CTRL + C)
signal.signal(signal.SIGINT, shutdown_pipeline)

# Start pipeline
pipeline.set_state(Gst.State.PLAYING)

# Listen to bus messages and check for error or EOS
bus = pipeline.get_bus()
msg = bus.timed_pop_filtered(
    Gst.CLOCK_TIME_NONE,
    Gst.MessageType.ERROR | Gst.MessageType.EOS
)

# Clean up when done
pipeline.set_state(Gst.State.NULL)
sys.exit(1)  # Exit the process to trigger supervisor restart if needed
