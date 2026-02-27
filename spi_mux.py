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
SPI_SPEED = 4000000

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

while True:
    readable, _, _ = select.select([sock1, sock2], [], [])

    for s in readable:
        data, _ = s.recvfrom(255)

        if s == sock1:
            stream_id = 1
        else:
            stream_id = 2

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
