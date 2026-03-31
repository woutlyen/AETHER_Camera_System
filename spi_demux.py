import spidev
import socket
import RPi.GPIO as GPIO

SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED = 3000000
CHUNK_SIZE = 1024

UDP_PORTS = {
    1: 6000,
    2: 6001,
}

udp_socks = {}
for sid, port in UDP_PORTS.items():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socks[sid] = sock

spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = 0

DRDY_PIN = 25
GPIO.setmode(GPIO.BCM)
GPIO.setup(DRDY_PIN, GPIO.IN)


def stm32_crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= (byte << 24)
        for _ in range(8):
            if crc & 0x80000000:
                crc = (crc << 1) ^ 0x04C11DB7
            else:
                crc <<= 1
            crc &= 0xFFFFFFFF
    return crc


def parse_and_forward(buffer):
    i = 0
    length_buf = len(buffer)

    while i < length_buf:

        # End-of-data marker
        if buffer[i] == 0x00:
            break

        # Need at least minimal packet + info bytes
        if i + 8 > length_buf:
            break

        length = buffer[i]

        total_size = length + 2  # include CC1200 info bytes

        if i + total_size > length_buf:
            print("Packet exceeds buffer -> should not happen")
            break

        stream_id = buffer[i + 1]

        payload_end = i + length - 4
        crc_received = int.from_bytes(
            buffer[payload_end:i + length], 'big'
        )

        header_payload = buffer[i:payload_end]
        crc_calc = stm32_crc32(header_payload)

        if crc_calc != crc_received:
            print("CRC mismatch -> dropping packet")
            i += total_size
            continue

        payload = buffer[i + 2:payload_end]
        payload = payload.rstrip(b'\x00')

        # Extract CC1200 info bytes
        info1 = buffer[i + length]
        info2 = buffer[i + length + 1]

        # ---- OPTIONAL: decode CC1200 status ----
        # Example (depends on your config):
        rssi = info1
        lqi_crc_ok = info2

        # You can log or use this:
        # print(f"RSSI: {rssi}, LQI/CRC: {lqi_crc_ok}")

        # Forward RTP payload
        if stream_id in udp_socks:
            udp_socks[stream_id].sendto(
                payload, ("127.0.0.1", UDP_PORTS[stream_id])
            )
            print(f"Forwarded packet for stream {stream_id} (size: {len(payload)})")

        i += total_size


print("SPI RX running...")

try:
    while True:
        #GPIO.wait_for_edge(DRDY_PIN, GPIO.RISING)

        while GPIO.input(DRDY_PIN) == 0:
            pass

        raw = spi.xfer2([0x00] * CHUNK_SIZE)
        buffer = bytes(raw)

        parse_and_forward(buffer)

        while GPIO.input(DRDY_PIN) == 1:
            pass

except KeyboardInterrupt:
    pass
finally:
    spi.close()
    GPIO.cleanup()