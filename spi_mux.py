"""
SPI Multiplexer - Aggregates RTP streams and transmits over SPI

Receives RTP packets from multiple UDP streams, tracks packet rates,
aggregates into SPI frames, and sends to UHF communication system.
Also reports packet rate statistics to supervisor via queue.
"""

import socket
import select
import spidev
import signal
import sys
import os
import time
import logging
import json

logging_level = os.getenv("LOGGING_LEVEL", "INFO").upper()

# Configure logging
logging.basicConfig(
    level=getattr(logging, logging_level, logging.INFO),
    format="%(asctime)s [SPI MULTIPLEXER] [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

if len(sys.argv) < 5:
    logger.error("Usage: spi_mux.py <spi_bus> <spi_device> <spi_speed> <udp_ports_comma_separated>")
    sys.exit(1)

SPI_BUS = int(sys.argv[1])
SPI_DEVICE = int(sys.argv[2])
SPI_SPEED = int(sys.argv[3])
UDP_PORTS = [int(p) for p in sys.argv[4].split(",")]

# File for sending RTP stats to supervisor
RTP_STATS_FILE = "/tmp/rtp_stats.json"

logging_enabled = os.getenv("LOGGING_ENABLED", "false").lower() == "true"

# SPI setup
spi = None
try:
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0
    logger.info(f"SPI initialized: bus={SPI_BUS}, device={SPI_DEVICE}, speed={SPI_SPEED}")
except Exception as e:
    logger.error(f"Failed to initialize SPI: {e}")
    sys.exit(1)

# Create UDP sockets dynamically
sockets = []
for i, port in enumerate(UDP_PORTS):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", port))
        sockets.append(sock)
        logger.info(f"UDP socket {i+1} listening on port {port}")
    except Exception as e:
        logger.error(f"Failed to bind UDP port {port}: {e}")

if not sockets:
    logger.error("No UDP sockets available")
    sys.exit(1)

# Statistics tracking
last_seq = {}
dropped_packets = {}
received_packets = {}
packet_rates = {}  # Packet rates per second

for i in range(len(sockets)):
    stream_id = i + 1
    last_seq[stream_id] = None
    dropped_packets[stream_id] = 0
    received_packets[stream_id] = 0
    packet_rates[stream_id] = 0

last_report_time = time.time()
tx_buffer = bytearray()
MAX_SPI_CHUNK = 1023

frame_count = 0


def shutdown_spi_mux(sig, frame):
    """Graceful shutdown of SPI multiplexer."""
    logger.info("Shutting down SPI multiplexer...")
    
    try:
        # Flush remaining buffer
        if tx_buffer:
            pad_len = MAX_SPI_CHUNK - len(tx_buffer)
            tx_buffer.extend(b'\x00' * pad_len)
            spi.xfer2(tx_buffer)
            logger.info(f"Flushed final SPI frame")
        
        # Report final stats
        logger.info(f"Total SPI frames transmitted: {frame_count}")
        
        # Close resources
        if spi:
            spi.close()
        for sock in sockets:
            sock.close()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_spi_mux)
signal.signal(signal.SIGTERM, shutdown_spi_mux)


def stm32_crc32(data: bytes) -> int:
    """
    Compute CRC32 compatible with STM32 hardware CRC unit.
    Polynomial: 0x04C11DB7, initial value: 0xFFFFFFFF
    """
    crc = 0xFFFFFFFF  # Fixed initial value
    
    for byte in data:
        crc ^= (byte << 24)  # Align byte to MSB
        
        for _ in range(8):
            if crc & 0x80000000:
                crc = (crc << 1) ^ 0x04C11DB7
            else:
                crc <<= 1
            
            crc &= 0xFFFFFFFF  # Keep 32-bit
    
    return crc  # No final XOR


def get_packet_rate(stream_id):
    """Get packet rate for a stream (capped at 255)."""
    rate = min(received_packets.get(stream_id, 0), 255)
    return rate


def send_rtp_stats_to_supervisor():
    """
    Send packet rate statistics to supervisor via JSON file.
    Called every second.
    """
    try:
        # Calculate rates for all streams (capped at 255)
        stats = {}
        for stream_id in sorted(received_packets.keys()):
            rate = get_packet_rate(stream_id)
            stats[f"stream_{stream_id}"] = rate
        
        # Write to file atomically (write to temp, then rename)
        temp_file = RTP_STATS_FILE + ".tmp"
        with open(temp_file, "w") as f:
            json.dump(stats, f)
        os.replace(temp_file, RTP_STATS_FILE)
        
        if logging_enabled:
            logger.debug(f"Sent RTP rates to supervisor: {stats}")
    except Exception as e:
        logger.error(f"Failed to send RTP stats: {e}")


def process_rtp_packet(stream_id, data):
    """
    Process a single RTP packet:
    - Extract sequence number
    - Track packet loss
    - Prepare for SPI transmission
    - Add to transmission buffer
    """
    global tx_buffer, frame_count
    
    if len(data) < 4:
        logger.warning(f"Stream {stream_id}: Packet too short ({len(data)} bytes)")
        return
    
    # Extract RTP sequence number (bytes 2-3)
    seq = (data[2] << 8) | data[3]
    
    received_packets[stream_id] += 1
    
    # Check for packet loss
    if last_seq[stream_id] is not None:
        expected = (last_seq[stream_id] + 1) & 0xFFFF
        if seq != expected:
            diff = (seq - expected) & 0xFFFF
            if diff > 0:
                dropped_packets[stream_id] += diff
                if logging_enabled:
                    logger.debug(f"Stream {stream_id}: Dropped {diff} RTP packet(s)")
    
    last_seq[stream_id] = seq
    
    # Pad to 4-byte alignment
    pad_len = (-(len(data) + 2)) % 4
    if pad_len:
        data += b'\x00' * pad_len
    
    # Build packet with header and CRC
    length = 2 + len(data) + 4  # header (2) + payload + CRC (4)
    header_and_payload = bytes([length, stream_id]) + data
    
    # Compute CRC
    crc = stm32_crc32(header_and_payload)
    
    # Append CRC as big-endian
    packet = header_and_payload + crc.to_bytes(4, 'big')
    
    # Check if packet fits in buffer
    if len(tx_buffer) + len(packet) > (MAX_SPI_CHUNK - 1):
        # Pad and send current buffer
        pad_len = MAX_SPI_CHUNK - len(tx_buffer)
        if pad_len > 0:
            tx_buffer.extend(b'\x00' * pad_len)
        
        try:
            spi.xfer2(tx_buffer)
            frame_count += 1
            if logging_enabled:
                logger.debug(f"Sent SPI frame {frame_count} ({len(tx_buffer)} bytes)")
        except Exception as e:
            logger.error(f"SPI transmission error: {e}")
        
        tx_buffer.clear()
    
    # Add packet to buffer
    tx_buffer.extend(packet)


def main():
    """Main SPI multiplexer loop."""
    logger.info("SPI multiplexer started")
    logger.info(f"Listening on {len(sockets)} UDP stream(s)")
    global last_report_time
    last_report_time = time.time()
    
    try:
        while True:
            # Use select for non-blocking socket monitoring
            readable, _, _ = select.select(sockets, [], [], 1.0)
            
            # Process received RTP packets
            for sock in readable:
                try:
                    data, _ = sock.recvfrom(255)
                    stream_id = sockets.index(sock) + 1
                    process_rtp_packet(stream_id, data)
                except Exception as e:
                    logger.error(f"Error receiving packet: {e}")
            
            # Check if time to send stats (every 1 second)
            now = time.time()
            if now - last_report_time >= 1.0:
                # Send statistics to supervisor
                send_rtp_stats_to_supervisor()
                
                # Log statistics if enabled
                if logging_enabled:
                    logger.info("---- RTP UDP Stats ----")
                    for stream_id in sorted(received_packets.keys()):
                        rx = received_packets[stream_id]
                        dropped = dropped_packets[stream_id]
                        logger.info(f"Stream {stream_id}: rx={rx}, dropped={dropped}")
                    logger.info(f"Total SPI frames: {frame_count}")
                    logger.info("-----------------------")
                
                # Reset counters for next period
                for stream_id in received_packets.keys():
                    received_packets[stream_id] = 0
                    dropped_packets[stream_id] = 0
                
                last_report_time = now
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        shutdown_spi_mux(None, None)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()