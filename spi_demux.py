import spidev
import socket
import RPi.GPIO as GPIO

SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED = 3000000
CHUNK_SIZE = 1024

# Camera streams
UDP_PORTS = {
    1: 6000,
    2: 6001,
}

# Sensor data message lengths (ID: data_length_in_bytes)
SENSOR_DATA_LENGTHS = {
    0x03: 8,   # GNSS_POSITION
    0x04: 4,   # EPS_BATTERY
    0x05: 8,   # CS_STATUS
    0x07: 8,   # IFS_ALTIMETER
    0x09: 8,   # IFS_TCOUPLE
    0x11: 8,   # IFS_TCOUPLE_INTERN
    0x13: 1,   # IFS_TCOUPLE_ERROR
    0x14: 4,   # IFS_STAGNATION
    0x15: 4,   # IFS_BW_CURRENTS
    0x16: 4,   # IFS_CGG_CURRENTS
    0x17: 2,   # IFS_MANIFOLD
    0x18: 8,   # IFS_ACCELERATION
    0x20: 6,   # IFS_ROTATION
}

SENSOR_NAMES = {
    0x03: "GNSS_POSITION",
    0x04: "EPS_BATTERY",
    0x05: "CS_STATUS",
    0x07: "IFS_ALTIMETER",
    0x09: "IFS_TCOUPLE",
    0x11: "IFS_TCOUPLE_INTERN",
    0x13: "IFS_TCOUPLE_ERROR",
    0x14: "IFS_STAGNATION",
    0x15: "IFS_BW_CURRENTS",
    0x16: "IFS_CGG_CURRENTS",
    0x17: "IFS_MANIFOLD",
    0x18: "IFS_ACCELERATION",
    0x20: "IFS_ROTATION",
}

udp_socks = {}
for sid, port in UDP_PORTS.items():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socks[sid] = sock

# Create sensor sockets (7000 + sensor_id)
sensor_socks = {}
for sensor_id in SENSOR_DATA_LENGTHS.keys():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sensor_socks[sensor_id] = sock

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


def bytes_to_uint16(msb: int, lsb: int) -> int:
    """Combine two bytes into unsigned 16-bit integer"""
    return (msb << 8) | lsb


def bytes_to_int16(msb: int, lsb: int) -> int:
    """Combine two bytes into signed 16-bit integer"""
    value = (msb << 8) | lsb
    if value & 0x8000:  # Check sign bit
        value = value - 0x10000
    return value


def bytes_to_int32(msb: int, b3: int, b2: int, lsb: int) -> int:
    """Combine four bytes into signed 32-bit integer"""
    value = (msb << 24) | (b3 << 16) | (b2 << 8) | lsb
    if value & 0x80000000:  # Check sign bit
        value = value - 0x100000000
    return value


def convert_sensor_data(sensor_id: int, data: bytes) -> list:
    """Convert raw sensor data to human-readable values"""
    values = []
    
    if sensor_id == 0x03:  # GNSS_POSITION
        # Latitude (uint24), Longitude (uint24), Altitude (uint16)
        lat = (data[0] << 16) | (data[1] << 8) | data[2]
        lon = (data[3] << 16) | (data[4] << 8) | data[5]
        alt = bytes_to_uint16(data[6], data[7])
        values = [lat / 1e6, lon / 1e6, alt]  # Convert to degrees/meters
        
    elif sensor_id == 0x04:  # EPS_BATTERY
        # Current (uint16), Voltage (uint16)
        current = bytes_to_uint16(data[0], data[1])
        voltage = bytes_to_uint16(data[2], data[3])
        values = [current, voltage]
        
    elif sensor_id == 0x05:  # CS_STATUS
        # 7x uint8, 1x status byte with bit flags
        values = [
            data[0],  # CPU_Usage %
            data[1],  # CPU_Temp °C
            data[2],  # RAM_Usage %
            data[3],  # eMMC_Usage %
            data[4],  # SD_Usage %
            data[5],  # Cam1_RTP
            data[6],  # Cam2_RTP
            data[7],  # Status byte (bit flags)
        ]
        
    elif sensor_id == 0x07:  # IFS_ALTIMETER
        # Temperature (int32, /100 °C), Pressure (int32, /100 mbar)
        temp_raw = bytes_to_int32(data[0], data[1], data[2], data[3])
        press_raw = bytes_to_int32(data[4], data[5], data[6], data[7])
        values = [temp_raw / 100.0, press_raw / 100.0]
        
    elif sensor_id == 0x09:  # IFS_TCOUPLE
        # 4x Temperature (int16, 0.25/LSB °C)
        for i in range(4):
            temp_raw = bytes_to_int16(data[i*2], data[i*2+1])
            temp_celsius = temp_raw * 0.25
            values.append(temp_celsius)
        
    elif sensor_id == 0x11:  # IFS_TCOUPLE_INTERN
        # 4x Temperature (int16, 0.0625/LSB °C)
        for i in range(4):
            temp_raw = bytes_to_int16(data[i*2], data[i*2+1])
            temp_celsius = temp_raw * 0.0625
            values.append(temp_celsius)
        
    elif sensor_id == 0x13:  # IFS_TCOUPLE_ERROR
        # Error flags (bit-mapped)
        values = [data[0]]
        
    elif sensor_id == 0x14:  # IFS_STAGNATION
        # Temperature (int16), Pressure (int16)
        temp_raw = bytes_to_int16(data[0], data[1])
        press_raw = bytes_to_int16(data[2], data[3])
        values = [temp_raw, press_raw]
        
    elif sensor_id == 0x15:  # IFS_BW_CURRENTS
        # 2x Current (uint16)
        current1 = bytes_to_uint16(data[0], data[1])
        current2 = bytes_to_uint16(data[2], data[3])
        values = [current1, current2]
        
    elif sensor_id == 0x16:  # IFS_CGG_CURRENTS
        # 2x Current (uint16)
        current1 = bytes_to_uint16(data[0], data[1])
        current2 = bytes_to_uint16(data[2], data[3])
        values = [current1, current2]
        
    elif sensor_id == 0x17:  # IFS_MANIFOLD
        # Pressure (uint16)
        pressure = bytes_to_uint16(data[0], data[1])
        values = [pressure]
        
    elif sensor_id == 0x18:  # IFS_ACCELERATION
        # 3x Acceleration (int16) + Temperature (int16)
        accel_z = bytes_to_int16(data[0], data[1])
        accel_y = bytes_to_int16(data[2], data[3])
        accel_x = bytes_to_int16(data[4], data[5])
        temp = bytes_to_int16(data[6], data[7])
        values = [accel_z, accel_y, accel_x, temp]
        
    elif sensor_id == 0x20:  # IFS_ROTATION
        # 3x Rotation Rate (int16)
        yaw = bytes_to_int16(data[0], data[1])
        roll = bytes_to_int16(data[2], data[3])
        pitch = bytes_to_int16(data[4], data[5])
        values = [yaw, roll, pitch]
    
    return values


def format_sensor_data(sensor_id: int, data: bytes, values: list) -> str:
    """Format sensor data for debug printing"""
    name = SENSOR_NAMES.get(sensor_id, "UNKNOWN")
    value_str = ', '.join(f'{v:.4g}' if isinstance(v, float) else str(v) for v in values)
    return f"[{name:20s}] ID: 0x{sensor_id:02X} | {value_str}"


def parse_sensor_payload(buffer: bytes, start_idx: int, end_idx: int):
    """Parse sensor data messages from payload (ID + data pairs)"""
    sensors = []
    i = start_idx
    
    while i < end_idx:
        if i >= len(buffer):
            break
        
        sensor_id = buffer[i]
        
        # Check if this is a known sensor ID
        if sensor_id not in SENSOR_DATA_LENGTHS:
            break
        
        data_len = SENSOR_DATA_LENGTHS[sensor_id]
        
        # Check if we have enough data
        if i + 1 + data_len > end_idx:
            break
        
        # Extract sensor data
        data = buffer[i + 1:i + 1 + data_len]
        sensors.append((sensor_id, data))
        
        i += 1 + data_len
    
    return sensors


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

        # Handle camera streams (stream_id 1 or 2)
        if stream_id in udp_socks:
            udp_socks[stream_id].sendto(
                payload, ("127.0.0.1", UDP_PORTS[stream_id])
            )
            print(f"[CAMERA] Stream {stream_id} | Size: {len(payload)} bytes")

        # Handle sensor data (stream_id 0 contains multiple sensor messages)
        elif stream_id == 0:
            sensors = parse_sensor_payload(buffer, i + 2, payload_end)
            
            print(f"\n--- Sensor Packet (RSSI: {rssi}) ---")
            for sensor_id, sensor_raw_data in sensors:
                # Convert raw data to human-readable values
                values = convert_sensor_data(sensor_id, sensor_raw_data)
                
                # Print formatted data
                print(f"  {format_sensor_data(sensor_id, sensor_raw_data, values)}")
                
                # Create comma-separated ASCII string
                csv_data = ','.join(
                    f'{v:.6g}' if isinstance(v, float) else str(v) 
                    for v in values
                )
                csv_bytes = csv_data.encode('ascii')
                
                # Send to sensor-specific UDP port
                port = 7000 + sensor_id
                sensor_socks[sensor_id].sendto(
                    csv_bytes, ("127.0.0.1", port)
                )
            print()

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