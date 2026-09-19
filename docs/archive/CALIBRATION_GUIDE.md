# Camera Calibration Guide

## Board Information

This calibration uses a checkerboard printed on A4 paper:
- **Printed squares**: 9 × 7
- **Inner corners**: 8 × 6 (what OpenCV detects)
- **Total corners**: 48

## Critical: Square Size

DO NOT use the nominal PDF size. Printers may scale the PDF differently
(fit to page, margins, etc.). Measure the actual printed square size
with a ruler or caliper.

### How to Measure

1. Print the checkerboard on A4 paper
2. Measure one edge of one printed square with a caliper
3. Record the value in millimeters
4. Enter this value in the Web UI (default is 25.0 mm)

## Step-by-Step Calibration

### 1. Setup
1. Connect USB UVC camera to Jetson
2. Run: `./scripts/run_calibration_studio.sh`
3. Open browser: `http://<JETSON_IP>:8000`
4. Click "Start Camera" - verify preview shows live image

### 2. Board Setup
1. Hold the checkerboard in front of the camera
2. Enter measured square size in mm
3. Click "Set"
4. Verify board shows "Detected" with 48/48 corners

### 3. Capture Frames
1. Move the board to different positions
2. Aim for 25-40 frames with varied pose:
   - Different distances (near/medium/far)
   - Different angles (yaw/pitch/roll)
   - Different positions (top/bottom/left/right/corners)
3. Wait for "48/48 ✓" indicator before each capture
4. Click "Capture Frame" to save

### 4. Run Calibration
1. After capturing ≥10 frames, click "Run Calibration"
2. Review overall RMS error
3. If high (> 1.0 px), exclude outlier frames and recalibrate

### 5. Verify
1. Switch to "Verify" tab
2. View Raw vs Undistorted (Side by Side)
3. Check that straight lines look straight in undistorted image

### 6. Export
1. Click "Export"
2. Save `camera.yaml` and `camera.json`
3. Use these files for downstream 6D pose pipeline

## Quality Guidelines

### RMS Error

| RMS (px) | Quality | Action |
|----------|---------|--------|
| < 0.5 | GOOD | Proceed |
| 0.5-0.8 | ACCEPTABLE | Proceed |
| 0.8-1.0 | WARNING | Consider recapture |
| > 1.0 | POOR | Exclude outliers, recapture |

### Common Issues

- **High RMS**: Check image sharpness (no motion blur), check board flatness
- **Detection fails often**: Improve lighting, increase contrast
- **Corners drift**: Camera may have autofocus - disable it

## Best Practices

1. **Disable autofocus**: Set manual focus before calibration
2. **Stable lighting**: Avoid changing lighting during capture
3. **Varied poses**: Capture frames at different angles and distances
4. **Sharp images**: Wait for camera to stabilize, avoid motion blur
5. **Board coverage**: Cover all areas of the frame (top, bottom, corners)
6. **Enough frames**: 25-40 frames for robust calibration
7. **Square measurement**: Measure with caliper, not ruler

## Advanced: Resolution Matching

Calibration is only valid at the resolution it was performed at.
If you change resolution at runtime, you must recalibrate.

## Troubleshooting

### Camera not found
```bash
ls -la /dev/video*
```
If no devices listed, check USB connection.

### Camera busy
```bash
fuser /dev/video0
# If PID returned:
kill -9 <PID>
```

### Board not detected
- Improve lighting (avoid backlit scenarios)
- Increase contrast
- Check that all 8×6 = 48 corners are visible
- Ensure board is flat (not bent)

### Browser can't connect
- Check firewall on Jetson
- Verify Jetson IP: `hostname -I`
- Test locally first: `curl http://127.0.0.1:8000/api/health`

### RMS too high
- Recapture blurry frames
- Check square size measurement
- Verify board is high quality (no warping)
