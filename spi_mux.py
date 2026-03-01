import socket
import select
import spidev
import signal
import sys
import os
import time

# SPI setup
SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED = 1000000

spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = 0

sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock1.bind(("127.0.0.1", 6000))

sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock2.bind(("127.0.0.1", 6001))

# Graceful shutdown
def shutdown_spi_mux(sig, frame):
    print("Shutting down SPI mux gracefully...")
    spi.close()  # Close the SPI connection
    sys.exit(0)  # Exit the process

# Register SIGINT handler (CTRL + C)
signal.signal(signal.SIGINT, shutdown_spi_mux)

# SPI Mux main loop
print("SPI MUX running")

# Check if logging is enabled by reading the environment variable
logging_enabled = os.getenv("LOGGING_ENABLED", "false").lower() == "true"

# RTP tracking per stream
last_seq = {1: None, 2: None}
dropped_packets = {1: 0, 2: 0}
received_packets = {1: 0, 2: 0}
last_report_time = time.time()

while True:
    readable, _, _ = select.select([sock1, sock2], [], [])

    for s in readable:
        data, _ = s.recvfrom(255)

        if len(data) < 4:
            continue  # Not a valid RTP packet

        # RTP sequence number is bytes 2-3
        seq = (data[2] << 8) | data[3]

        if s == sock1:
            stream_id = 1
        else:
            stream_id = 2

        # Update packet counts and check for drops
        received_packets[stream_id] += 1

        if last_seq[stream_id] is not None:
            expected = (last_seq[stream_id] + 1) & 0xFFFF  # wrap at 16-bit
            if seq != expected:
                diff = (seq - expected) & 0xFFFF
                if diff > 0:
                    dropped_packets[stream_id] += diff
                    if logging_enabled:
                        print(f"[Stream {stream_id}] dropped {diff} RTP packet(s)")

        last_seq[stream_id] = seq

        length = len(data)+2
        
        # Build packet: [length_hi][length_lo][stream_id][data...]
        packet = bytes([length,stream_id]) + data
        
        
        # Print source ID and packet length
        if logging_enabled:
            print(f"Source ID: {stream_id}, Packet Length: {length} bytes")
            hex_string = " ".join(f"{b:02X}" for b in packet)
            print(f"SPI Packet (hex): {hex_string}")
            print("-" * 60)
            
        spi.xfer2(packet)
        time.sleep(0.001)  # Small delay to prevent overwhelming the SPI bus

        # Periodically report packet loss statistics
        now = time.time()
        if now - last_report_time >= 1:
            print("---- RTP UDP Stats ----")
            for sid in (1, 2):
                print(f"Stream {sid}: received={received_packets[sid]}, dropped={dropped_packets[sid]}")
                received_packets[sid] = 0
                dropped_packets[sid] = 0
            print("-----------------------")
            last_report_time = now
