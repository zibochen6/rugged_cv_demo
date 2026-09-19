# Camera Calibration Studio - Architecture

## Overview

Web-based camera calibration system running on NVIDIA Jetson, designed for
remote calibration via web browser without SSH or HDMI.

## System Layout

```
┌─────────────────────────────────────────────────────────────┐
│                     Browser (Remote PC)                       │
│                   React + TypeScript + Vite                  │
│   - Live MJPEG preview (< 100ms latency)                    │
│   - Capture control / session management                     │
│   - Calibration result review                               │
│   - Export YAML/JSON                                         │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP/REST + MJPEG
                             │
┌────────────────────────────▼────────────────────────────────┐
│                    FastAPI Backend (Python 3.8)              │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  CameraManager                        │    │
│  │  - Single VideoCapture owner (singleton)             │    │
│  │  - Capture thread with stop_event                    │    │
│  │  - Atomic latest_frame access                        │    │
│  │  - Graceful shutdown (Ctrl+C safe)                    │    │
│  └─────────────┬───────────────────┬───────────────────┘    │
│                │                   │                         │
│                ▼                   ▼                         │
│  ┌──────────────────┐   ┌────────────────────┐              │
│  │  MjpegEncoder     │   │  Calibration        │              │
│  │  - 960×540 @ Q75   │   │  - Board detection  │              │
│  │  - Per-client buf  │   │  - calibrateCamera  │              │
│  │  - Latest drop     │   │  - Reprojection err │              │
│  └──────────────────┘   └────────────────────┘              │
│                                                              │
│  Session Manager → data/calibrations/session_*/              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
                    USB Camera /dev/videoX
```

## Camera Lifecycle

### State Machine

```
DISCONNECTED ──start()──> OPENING ──success──> RUNNING
   ▲                          │                  │
   │                          └──failure──> ERROR
   │                                              │
   │                                          restart()
   │                                              ▼
   └────────────stop()/release()────────────  OPENING
```

### Critical Rules

1. **Single VideoCapture**: Only one camera owner exists globally
2. **No HTTP-triggered open/release**: Camera lifecycle owned by backend,
   not by any individual HTTP connection
3. **Idempotent start/stop**: Calling stop() multiple times is safe
4. **Graceful shutdown**: All resources released within 3 seconds of Ctrl+C

### Graceful Shutdown Sequence

```
SIGINT/SIGTERM
   │
   ▼
Uvicorn receives signal
   │
   ▼
FastAPI lifespan shutdown
   │
   ├──> MjpegEncoder.stop()
   │      - stop_event.set()
   │      - join thread (timeout 3s)
   │
   ├──> CameraManager.stop()
   │      - stop_event.set()
   │      - join capture thread (timeout 3s)
   │      - VideoCapture.release()
   │
   └──> Process exits
```

After shutdown:
- `fuser /dev/videoX` returns nothing
- Process can immediately restart
- Other programs can open camera

## MJPEG Streaming Design

### Goal: < 100ms end-to-end latency (LAN)

### Pipeline
```
Camera (30 FPS)
   │ cap.read()
   ▼
latest_frame (shared buffer)
   │ get_latest_frame()
   ▼
Encoder thread (single)
   │ resize to 960×540
   │ imencode JPEG (Q75)
   ▼
Per-client ringbuffer (size=1)
   │ broadcast
   ▼
HTTP response (multipart/x-mixed-replace)
   │ TCP (LAN ~100Mbps)
   ▼
Browser <img> decoder
```

### Latency Budget

| Stage | Latency |
|-------|---------|
| Camera capture | 33ms |
| JPEG encode (960×540) | ~5-8ms |
| TCP transmit (LAN) | ~2ms |
| Browser decode | ~3-5ms |
| Render paint | ~10-16ms |
| **Total** | **~55-70ms** |

### Key Optimizations

1. **Preview downscale**: 960×540 instead of 1280×720 (44% less pixels)
2. **Per-client buffer size=1**: Slow clients don't block others
3. **Latest-frame drop**: Old frames skipped
4. **Single encoder thread**: No per-client encoding overhead
5. **No-Cache headers**: Browser doesn't cache JPEG

## Calibration Workflow

### Board Definition
- **9×7 squares printed** → **8×6 inner corners** for OpenCV
- OpenCV uses `pattern_size = (8, 6)` (inner corners, not squares!)

### State Machine

```
IDLE → CAPTURING → READY → CALIBRATING → CALIBRATED → VERIFYING → EXPORTING
```

### Quality Thresholds

| RMS | Status |
|-----|--------|
| < 0.5 px | GOOD |
| 0.5-0.8 px | ACCEPTABLE |
| 0.8-1.0 px | WARNING |
| > 1.0 px | POOR/OUTLIER |

### Output Files

`data/calibrations/session_YYYYMMDD_HHMMSS_xxxxxxxx/`:
```
├── session.json          # Session metadata
├── frames/
│   ├── frame_0001.jpg
│   ├── frame_0002.jpg
│   └── ...
├── result.json            # Calibration result (machine readable)
├── camera.yaml            # Final calibration file
└── camera.json            # Same as YAML but JSON
```

## API Endpoints

### System
- `GET /api/health` - Service status
- `GET /api/system/info` - Python/OpenCV versions

### Camera
- `GET /api/camera/devices` - Available devices
- `GET /api/camera/status` - Current state
- `POST /api/camera/start` - Start capture
- `POST /api/camera/stop` - Stop capture
- `POST /api/camera/restart` - Restart capture
- `GET /api/camera/stream.mjpg` - MJPEG stream

### Calibration
- `GET /api/calibration/board` - Board config
- `POST /api/calibration/session/new` - Create session
- `GET /api/calibration/session` - Get current session
- `POST /api/calibration/session/square_size` - Set square size
- `POST /api/calibration/capture` - Capture frame
- `GET /api/calibration/frames` - List frames
- `DELETE /api/calibration/frames/{id}` - Delete frame
- `POST /api/calibration/frames/{id}/exclude` - Exclude frame
- `POST /api/calibration/frames/{id}/include` - Include frame
- `POST /api/calibration/run` - Run calibration
- `GET /api/calibration/result` - Get result
- `GET /api/calibration/export/yaml` - Export YAML
- `GET /api/calibration/export/json` - Export JSON
- `GET /api/calibration/verify/undistort` - Get undistorted frame

## File Structure

```
backend/
├── app/
│   ├── main.py                # FastAPI app + lifespan
│   ├── config.py              # YAML config loader
│   ├── errors.py              # Unified error types
│   ├── camera/
│   │   ├── manager.py         # CameraManager singleton
│   │   ├── discovery.py       # Device enumeration
│   │   └── models.py          # CameraState enum, etc.
│   ├── calibration/
│   │   ├── board.py           # BoardSpec (8×6 inner)
│   │   ├── detector.py        # Chessboard detection
│   │   ├── calibrator.py      # calibrateCamera
│   │   ├── reprojection.py    # Error calculation
│   │   └── session.py         # Session management
│   ├── streaming/
│   │   └── mjpeg.py           # MJPEG encoder
│   └── api/
│       ├── system.py
│       ├── camera.py
│       └── calibration.py
└── tests/

frontend/
├── src/
│   ├── App.tsx                # Main UI
│   ├── api/client.ts          # API client
│   ├── types/index.ts         # Type definitions
│   └── ...
├── package.json
└── vite.config.ts
```
