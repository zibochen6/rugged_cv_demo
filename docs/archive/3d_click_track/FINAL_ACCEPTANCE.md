# Phase 10 — Final Acceptance Checklist (§84)

Date: 2025-09-01 · Branch: `feat/3d-click-track` · Baseline tag: `baseline-efficienttam-working`

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Old EfficientTAM regression all PASS | ✅ | `scripts/run_regression.sh` re-run after every phase; final run ALL PASS |
| 2 | Monocular metric depth runs | ✅ | 10A: DAV2-Metric-Indoor-Small eager CUDA; 11.8/17.6/22.3 FPS fp32/bf16/fp16 @720p |
| 3 | Depth metric or clearly marked | ✅ | metric indoor model (meters); UI tags `[APPROXIMATE: estimated K]` until calibrated; `benchmarks/depth_accuracy.md` pending tape GT |
| 4 | Mask + depth robust sampling | ✅ | 10B: erode→valid→20-80 trim→median; 30/30 frames valid; synthetic outlier/low-coverage gates |
| 5 | Camera intrinsics support | ✅ | `configs/3d_pipeline.yaml` K + distortion + calibrated flag; `scaled_to()`; iterative undistort interface |
| 6 | XYZ correct | ✅ | 10C/10D unit gate; dog.mp4 XYZ stable (std 0.04/0.03/0.22 m over 30 frames) |
| 7 | XYZ temporal filtering | ✅ | Kalman3D (X,Y,Z,V) + Mahalanobis/velocity gating + speed clamp + re-anchor; 35 m spike rejected; raw vs filtered in `--debug` |
| 8 | Trajectory bounded | ✅ | deque(maxlen=300); eviction unit-tested |
| 9 | BEV works | ✅ | pure cv2 top-down, grid/ranges from config; mapping unit gate |
| 10 | Real camera support | ⚠️ code-ready | `usb:` = proven 2D-demo V4L2 path; no camera attached this session → live gate pending hardware (§67) |
| 11 | Click-to-Track UX unchanged | ✅ | same keys/mouse semantics (+R-click negative); left-click rebase identical |
| 12 | Full pipeline benchmark | ✅ | `benchmarks/results_3d*.md`: full 3D = 12.3 FPS @depth_every=2 (Good ≥12; target ≥10) |
| 13 | 30-min memory stability | ✅ | live pattern: RSS slope +0.37 MB/min, CUDA flat, FPS 12.6→12.7, no OOM/crash; stress-mode growth = known issue |
| 14 | README | ✅ | §12 3D Click-to-Track + architecture + limitations + known issues |

## Extra gates performed

- Depth dtype drift < 0.1% (bf16/fp16 medians vs fp32)
- Invalid depth ⇒ hold last state + confidence decay, never Z=0 (§58)
- Mask-lost handling (§59); depth outlier gating (§60)
- Bounded disk: seed tmpdir cleaned per rebase
- CSV logging `logs/3d_pipeline/trajectory*.csv` (§65); `--debug` overlays (§66)
- Distance scale-calibration tool + auto-merge (`scripts/calibrate_distance.py`, §45)

## Not done / future (documented)

- Live-camera 30-min gate + tape-measure GT (§45/§82) — needs USB camera
- 2-hour soak (§57)
- Stress-mode (rapid re-click + depth) delayed native-heap retention
- Depth TensorRT (§52) — only if live FPS < 10 (currently 12.3)
