import gi
import sys
import signal
import socket
import time

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

if len(sys.argv) != 5:
    print("Usage: udp_mjpeg.py <udp_port_in> <udp_address_out> <udp_port_out> <label>")
    sys.exit(1)

udp_port_in = sys.argv[1]
udp_address_out = sys.argv[2]
udp_port_out = sys.argv[3]
label = sys.argv[4]

MAX_PAYLOAD = 8192

frame_count = 0
fps = 0
last_time = time.time()
frames_in_window = 0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_metadata():
    global fps, frame_count

    # Format: AB CD EF <fps>,<frame_count> FE ED
    payload = f"{fps},{frame_count}".encode()

    packet = b'\xAB\xCD\xEF' + payload + b'\xFE\xED'

    sock.sendto(packet, (udp_address_out, int(udp_port_out)))

def send_frame(frame_bytes):
    for i in range(0, len(frame_bytes), MAX_PAYLOAD):
        chunk = frame_bytes[i:i + MAX_PAYLOAD]
        sock.sendto(chunk, (udp_address_out, int(udp_port_out)))


def on_new_sample(appsink):
    global frame_count, fps, last_time, frames_in_window
    sample = appsink.emit("pull-sample")
    buffer = sample.get_buffer()
    print(buffer)

    success, map_info = buffer.map(Gst.MapFlags.READ)
    if not success:
        return Gst.FlowReturn.ERROR

    try:
        frame_data = map_info.data
        
        # Send frame
        send_frame(frame_data)

        # Update counters
        frame_count += 1
        frames_in_window += 1

        now = time.time()
        if now - last_time >= 1.0:
            fps = frames_in_window
            frames_in_window = 0
            last_time = now

        # Send metadata packet
        send_metadata()

    finally:
        buffer.unmap(map_info)

    return Gst.FlowReturn.OK


pipeline = Gst.parse_launch(f"""
udpsrc port={udp_port_in} !
application/x-rtp, encoding-name=H264, payload=96 !
rtpjitterbuffer !
rtph264depay !
h264parse !
avdec_h264 !
jpegenc quality=50 !
appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true
""")

appsink = pipeline.get_by_name("sink")
appsink.connect("new-sample", on_new_sample)

def shutdown_pipeline(sig, frame):
    print(f"Shutting down {label} pipeline gracefully...")
    pipeline.set_state(Gst.State.NULL)
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_pipeline)

ret = pipeline.set_state(Gst.State.PLAYING)
print("Set state result:", ret)

bus = pipeline.get_bus()
msg = bus.timed_pop_filtered(
    Gst.CLOCK_TIME_NONE,
    Gst.MessageType.ERROR | Gst.MessageType.EOS
)

pipeline.set_state(Gst.State.NULL)
sock.close()
sys.exit(1)