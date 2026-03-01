import socket
import select
import spidev
import signal
import sys
import os
import time

if len(sys.argv) < 5:
    print("Usage: spi_mux.py <spi_bus> <spi_device> <spi_speed> <udp_ports_comma_separated>")
    sys.exit(1)

SPI_BUS = int(sys.argv[1])
SPI_DEVICE = int(sys.argv[2])
SPI_SPEED = int(sys.argv[3])
UDP_PORTS = [int(p) for p in sys.argv[4].split(",")]

# SPI setup
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = 0

# Create UDP sockets dynamically
sockets = []
for port in UDP_PORTS:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    sockets.append(sock)

print("SPI MUX running")

logging_enabled = os.getenv("LOGGING_ENABLED", "false").lower() == "true"

last_seq = {}
dropped_packets = {}
received_packets = {}

for i in range(len(sockets)):
    stream_id = i + 1
    last_seq[stream_id] = None
    dropped_packets[stream_id] = 0
    received_packets[stream_id] = 0

last_report_time = time.time()


def shutdown_spi_mux(sig, frame):
    print("Shutting down SPI mux gracefully...")
    spi.close()
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_spi_mux)

while True:
    readable, _, _ = select.select(sockets, [], [])

    for s in readable:
        data, _ = s.recvfrom(255)

        if len(data) < 4:
            continue

        stream_id = sockets.index(s) + 1
        seq = (data[2] << 8) | data[3]

        received_packets[stream_id] += 1

        if last_seq[stream_id] is not None:
            expected = (last_seq[stream_id] + 1) & 0xFFFF
            if seq != expected:
                diff = (seq - expected) & 0xFFFF
                if diff > 0:
                    dropped_packets[stream_id] += diff
                    if logging_enabled:
                        print(f"[Stream {stream_id}] dropped {diff} RTP packet(s)")

        last_seq[stream_id] = seq

        length = len(data) + 2
        packet = bytes([length, stream_id]) + data

        if logging_enabled:
            print(f"Source ID: {stream_id}, Packet Length: {length}")

        spi.xfer2(packet)

    now = time.time()
    if now - last_report_time >= 1:
        print("---- RTP UDP Stats ----")
        for sid in received_packets:
            print(f"Stream {sid}: received={received_packets[sid]}, dropped={dropped_packets[sid]}")
            received_packets[sid] = 0
            dropped_packets[sid] = 0
        print("-----------------------")
        last_report_time = now