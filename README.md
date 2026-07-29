# AETHER Camera System

On-board camera and video-downlink software for the **AETHER** platform, running on a
Raspberry Pi Compute Module 4 (CM4). It records H.264 video from two cameras to local
storage, downscales and packetizes a low-bitrate live stream, and forwards it over SPI to
a UHF communication system for downlink. A supervisor process manages the whole system,
reports telemetry, and takes commands from the On-Board Computer (OBC) over a CAN bus.

The system is designed to run **headless and autonomously**: it self-checks its hardware
at boot, recovers from failures, hot-swaps its SD card, and keeps recording even when the
OBC is unreachable.

---

## Contents

- [AETHER Camera System](#aether-camera-system)
  - [Contents](#contents)
  - [Features](#features)
  - [Architecture](#architecture)
  - [Data flow](#data-flow)
  - [Repository layout](#repository-layout)
  - [Configuration](#configuration)
  - [CAN protocol](#can-protocol)
  - [Telemetry (`CS_STATUS`)](#telemetry-cs_status)
  - [Fallback mode](#fallback-mode)
  - [Health monitoring \& recovery](#health-monitoring--recovery)

---

## Features

- **Dual-camera capture** — two OV5647 sensors on an I²C mux, each recorded as H.264 at
  1280×720 / 30 fps via the CM4's hardware encoder (`v4l2h264enc`).
- **Redundant recording** — every stream is written to onboard eMMC and, when present, to
  a removable SD card simultaneously.
- **Live low-bitrate downlink** — a second branch of each pipeline downscales to
  640×360 / 8 fps and re-encodes at ~165 kbps for transmission over a bandwidth-limited
  UHF link.
- **SPI multiplexer** — aggregates the RTP/UDP streams from all cameras into padded SPI
  frames, each packet tagged with a header and an STM32-hardware-compatible CRC32.
- **CAN command & telemetry** — receives enable/disable/ping commands from the OBC and
  transmits a system status frame once per second.
- **Health & recovery** — a boot-time hardware check, runtime data-flow monitoring
  (recording stalls, dropped RTP packets), SD-card hot-swap detection, and a
  critical-failure power-cycle request.
- **Autonomous fallback** — if the OBC never answers, the system streams on its own for a
  fixed window instead of sitting idle.
- **Config-driven & scalable** — cameras, ports, and SPI settings live in
  [config.json](config.json); adding a camera is a config change, not a code change.

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │            Raspberry Pi CM4 (camera)          │
                    │                                               │
  ov5647 (cam1) ───▶│  camera.py ──▶ eMMC + SD (H.264 recording)    │
  ov5647 (cam2) ───▶│      │                                        │
                    │      └── RTP/H.264 ──▶ UDP :6000/:6001         │
                    │                             │                  │
                    │                       spi_mux.py               │
                    │                             │  (framing+CRC32) │
                    │                             ▼                  │
                    │                          SPI bus  ─────────────┼──▶ UHF comms ──▶ ground
                    │                                               │
                    │  supervisor.py  ◀── health_check.py (boot)     │
                    │      │  ▲                                      │
                    │      │  └── can_listener.py                    │
                    └──────┼──────────────┬───────────────────────── ┘
                           │ CS_STATUS    │ commands / replies
                           ▼ (telemetry)  ▲
                        ┌──────────────────────┐
                        │   CAN bus  ◀───▶ OBC  │
                        └──────────────────────┘
```

The **supervisor** is the top-level orchestrator. It reads [config.json](config.json),
starts/stops the camera, SPI-mux, and CAN-listener subprocesses to match the desired
state, monitors their health, and speaks to the OBC over CAN. Every other module is a
worker that the supervisor launches and watches.

---

## Data flow

**Video path (per camera)** — built in [camera.py](camera.py):

1. `libcamerasrc` captures 1280×720 NV12 @ 30 fps.
2. A `tee` splits the stream into two branches:
   - **Record branch** → hardware `v4l2h264enc` → `filesink` to eMMC (`camera{N}.h264`),
     and a second `filesink` to the SD card when one is mounted. Files roll over to
     `camera{N}_1.h264`, `_2.h264`, … as they fill.
   - **Stream branch** → downscale to 640×360, drop to 8 fps → `x264enc` (zerolatency,
     ~165 kbps, sliced) → `rtph264pay` → `udpsink` on `127.0.0.1:<udp_port>`.

**Downlink path** — handled by [spi_mux.py](spi_mux.py):

1. Binds a UDP socket per camera port and reads incoming RTP packets.
2. Tracks per-stream RTP sequence numbers to count received/dropped packets.
3. Wraps each packet as `[length][stream_id][rtp payload][CRC32]`, 4-byte aligned, with a
   CRC compatible with the STM32 hardware CRC unit (poly `0x04C11DB7`, init `0xFFFFFFFF`).
4. Packs packets into ≤1023-byte SPI frames, pads, and transmits over the SPI bus.
5. Writes per-stream packet rates to `/tmp/rtp_stats.json` once per second for the
   supervisor to fold into telemetry.

**Control path** — handled by [can_listener.py](can_listener.py) and
[supervisor.py](supervisor.py): the listener applies OBC commands by editing
[config.json](config.json) (atomically), and the supervisor reconciles running processes
against that config on its next loop.

---

## Repository layout

| File | Role |
|------|------|
| [supervisor.py](supervisor.py) | Main orchestrator: process lifecycle, health monitoring, SD hot-swap, telemetry, fallback logic. |
| [camera.py](camera.py) | Builds and runs one camera's GStreamer pipeline (record + stream). Launched once per camera. |
| [spi_mux.py](spi_mux.py) | Aggregates RTP/UDP streams and transmits framed, CRC-tagged packets over SPI. |
| [health_check.py](health_check.py) | Boot-time hardware probe (SD, CAN, OBC, cameras); writes results into config and sends `CS_WAKE_UP`. |
| [can_listener.py](can_listener.py) | Receives OBC commands over CAN and updates the config. |
| [can_constants.py](can_constants.py) | CAN message IDs, response codes, command mapping, and status-byte bit definitions. |
| [sd_card.py](sd_card.py) | Shared SD-card detection helpers (dynamic mount-point resolution via `/proc/mounts`). |
| [config.json](config.json) | Runtime configuration and hardware/system state. |
| [services/](services/) | `systemd` unit files and the SD0 device-tree overlay. |
| [install_guide.txt](install_guide.txt) | Step-by-step first-time setup on the CM4. |

---

## Configuration

All runtime behavior is driven by [config.json](config.json):

```json
{
    "features": { "logging_level": "INFO" },
    "system": {
        "boot_retry_count": 0,
        "max_retries": 1,
        "hardware_status": {
            "sd_card": false, "can": false, "obc": false,
            "cam1": false, "cam2": false
        }
    },
    "cameras": [
        {
            "enabled": false,
            "name": "cam1",
            "camera_path": "/base/soc/i2c0mux/i2c@1/ov5647@36",
            "stream_index": 0,
            "udp_port": 6000
        }
    ],
    "spi": { "enabled": false, "bus": 0, "device": 0, "speed": 3000000 }
}
```

- **`features.logging_level`** — `DEBUG` / `INFO` / `WARNING` / `ERROR`, propagated to all
  subprocesses via the `LOGGING_LEVEL` environment variable.
- **`system.hardware_status`** — written by [health_check.py](health_check.py) at boot;
  the supervisor only starts hardware that was actually detected.
- **`system.boot_retry_count` / `max_retries`** — control the boot self-check retry/reboot
  loop before the system continues in degraded mode.
- **`cameras[]`** — one entry per camera. `enabled` is toggled by OBC commands;
  `camera_path` is the libcamera device path; `udp_port` is the local RTP port feeding the
  SPI mux. Add a camera by adding an entry here — no code changes required.
- **`spi`** — SPI bus/device/clock for the downlink and whether the mux runs.

> Note: `enabled` (config intent) is ANDed with `hardware_status` (what was detected), so a
> camera only runs when it is both requested and physically present.

---

## CAN protocol

All CAN frames are standard (11-bit) IDs, big-endian, at 500 kbit/s. IDs and codes are
defined in [can_constants.py](can_constants.py).

**Boot handshake**

| Message | ID | Direction | Purpose |
|---------|----|-----------|---------|
| `CS_WAKE_UP` | `0x0BF` | CS → OBC | Announce readiness at boot (retried up to 10×). |
| `CS_WAKE_UP_REPLY` | `0x4BF` | OBC → CS | OBC acknowledges (`CS_REP_OK` = `0xFF`). |

**Commands from the OBC** (each gets a reply of `CS_REP_OK`/`CS_REP_NOK`)

| Command | ID | Reply ID | Action |
|---------|----|----------|--------|
| `CS_PING` | `0x490` | `0x090` | Liveness check. |
| `CS_CAMERA1_ENABLE` / `DISABLE` | `0x491` / `0x492` | `0x091` / `0x092` | Toggle camera 1. |
| `CS_CAMERA2_ENABLE` / `DISABLE` | `0x493` / `0x494` | `0x093` / `0x094` | Toggle camera 2. |
| `CS_SPI_ENABLE` / `DISABLE` | `0x495` / `0x496` | `0x095` / `0x096` | Toggle the SPI mux. |

**Sent by the supervisor**

| Message | ID | Direction | Purpose |
|---------|----|-----------|---------|
| `CS_STATUS` | `0x505` | CS → OBC | Telemetry, every 1 s (see below). |
| `CS_POWER_CYCLE` | `0x0BE` | CS → OBC | Request a power cycle on critical failure. |

---

## Telemetry (`CS_STATUS`)

Sent once per second as 8 data bytes:

| Byte | Field | Description |
|------|-------|-------------|
| 0 | `cpu_usage` | CPU load, % |
| 1 | `cpu_temp` | CPU temperature, °C |
| 2 | `ram_usage` | RAM usage, % |
| 3 | `emmc_usage` | eMMC usage, % |
| 4 | `sd_usage` | SD card usage, % (0 if absent) |
| 5 | `cam1_rtp` | Camera 1 RTP packet rate (capped at 255) |
| 6 | `cam2_rtp` | Camera 2 RTP packet rate (capped at 255) |
| 7 | `status_byte` | Bit-field, see below |

**Status byte (byte 7)**

| Bit | Name | Meaning |
|-----|------|---------|
| 0 | `CAM1` | Camera 1 process running |
| 1 | `CAM2` | Camera 2 process running |
| 2 | `SD` | SD card mounted |
| 3 | `SPI_MUX` | SPI multiplexer running |
| 4 | `CAN` | CAN controller present |
| 5 | `CAN_BUS` | CS ↔ OBC link up |
| 6 | `FALLBACK` | System in fallback mode |
| 7 | — | Reserved |

---

## Fallback mode

If the OBC does not answer the boot handshake (`obc` is not available), the supervisor
enters **fallback mode**: it ignores the config's `enabled` flags and instead runs every
detected camera plus the SPI mux for a fixed window (`FALLBACK_DURATION`, 30 minutes), so
the system keeps recording and downlinking on its own. When the window expires it stops
cameras and the mux and waits for the OBC. Fallback state is reported in the telemetry
status byte (bit 6).

---

## Health monitoring & recovery

The supervisor continuously checks that the system is not just *running* but *working*:

- **Camera data flow** — confirms each camera's `.h264` file is still growing; a stall
  beyond the limit (30 s, or 60 s right after start) is treated as a critical failure.
- **SPI data flow** — confirms the mux is still receiving RTP packets when cameras are
  enabled; 30 s of silence is a critical failure.
- **Crash recovery** — any subprocess that exits is restarted on the next supervisor loop.
- **SD hot-swap** — inserting or removing the SD card transparently restarts the affected
  cameras so recording follows the card.
- **Critical failure** — sends `CS_POWER_CYCLE` (resent every 30 s while the fault
  persists) so the OBC/watchdog can power-cycle the system.
