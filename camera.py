import sys
import os
import gi
import signal

gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

if len(sys.argv) != 5:
    print("Usage: camera.py <camera_name> <stream_index> <udp_port> <label>")
    sys.exit(1)

camera_name = sys.argv[1]
stream_index = sys.argv[2]
udp_port = sys.argv[3]
label = sys.argv[4]

base_path = '/home/pi/Camera'
base_filename_eMMC = f'Stream_{stream_index}_eMMC'
base_filename_SD = f'Stream_{stream_index}_SD'


def get_next_available_filename(base_path, base_filename):
    index = 0
    while True:
        candidate = (
            f"{base_path}/{base_filename}_{index}.h264"
            if index > 0 else
            f"{base_path}/{base_filename}.h264"
        )

        if not os.path.exists(candidate) or os.path.getsize(candidate) == 0:
            return candidate

        index += 1


eMMC_filename = get_next_available_filename(base_path, base_filename_eMMC)
SD_filename = get_next_available_filename(base_path, base_filename_SD)

pipeline = Gst.parse_launch(f"""
libcamerasrc camera-name={camera_name} !
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
x264enc tune=zerolatency bitrate=200 speed-preset=superfast key-int-max=8 intra-refresh=true bframes=0 aud=true option-string="slice-max-size=236" !
video/x-h264,stream-format=byte-stream,alignment=au !
h264parse config-interval=1 !
rtph264pay pt=96 mtu=242 config-interval=1 !
udpsink host=127.0.0.1 port={udp_port} sync=false async=false
""")

def shutdown_pipeline(sig, frame):
    print(f"Shutting down {label} pipeline gracefully...")
    pipeline.set_state(Gst.State.NULL)
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_pipeline)

pipeline.set_state(Gst.State.PLAYING)

bus = pipeline.get_bus()
msg = bus.timed_pop_filtered(
    Gst.CLOCK_TIME_NONE,
    Gst.MessageType.ERROR | Gst.MessageType.EOS
)

pipeline.set_state(Gst.State.NULL)
sys.exit(1)