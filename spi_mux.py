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

count = 0 
tx_buffer = bytearray()
MAX_SPI_CHUNK = 1023


def shutdown_spi_mux(sig, frame):
    print("Shutting down SPI mux gracefully...")

    if tx_buffer:
        pad_len = MAX_SPI_CHUNK - len(tx_buffer)
        tx_buffer.extend(b'\x00' * pad_len)
        spi.xfer2(tx_buffer)

    print(count)
    spi.close()
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_spi_mux)

def stm32_crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF  # fixed initial value

    for byte in data:
        crc ^= (byte << 24)  # align byte to MSB

        for _ in range(8):
            if crc & 0x80000000:
                crc = (crc << 1) ^ 0x04C11DB7
            else:
                crc <<= 1

            crc &= 0xFFFFFFFF  # keep 32-bit

    return crc  # NO final XOR

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

        # Pad to 4-byte alignment
        pad_len = (-(len(data)+2)) % 4
        if pad_len:
            data += b'\x00' * pad_len

        length = 2 + len(data) + 4  # header + payload + CRC 

        header_and_payload = bytes([length, stream_id]) + data

        crc = stm32_crc32(header_and_payload)

        # Append CRC as big-endian (STM32 CRC register is MSB-first)
        packet = header_and_payload + crc.to_bytes(4, 'big')

        # if logging_enabled:
        #     print(f"Source ID: {stream_id}, Packet Length: {length}")

        # If packet doesn't fit → pad + send
        if len(tx_buffer) + len(packet) > (MAX_SPI_CHUNK-1):
            # Pad with zeros to exactly 1023 bytes
            pad_len = MAX_SPI_CHUNK - len(tx_buffer)
            if pad_len > 0:
                tx_buffer.extend(b'\x00' * pad_len)

            spi.xfer2(tx_buffer)
            tx_buffer.clear()

        # Add packet to buffer
        tx_buffer.extend(packet)

        if (length > 125):
            count += 1
        count += 1

        # print("SPI TX:", " ".join(str(b) for b in packet))
        # print("SPI TX:", " ".join(f"{b:02X}" for b in packet))
        # print(f"CRC32: {crc} (0x{crc:08X})")

        # time.sleep(0.001)     # Normal Delay

        now = time.time()
        if now - last_report_time >= 1:
            print("---- RTP UDP Stats ----")
            for sid in received_packets:

                print(f"Stream {sid}: received={received_packets[sid]}, dropped={dropped_packets[sid]}")
                
                received_packets[sid] = 0
                dropped_packets[sid] = 0
            print(count)
            print("-----------------------")
            last_report_time = now