"""Forklift rear-view monocular depth warning subsystem.

Modules:
  config            - warning.yaml loading with defaults
  depth_backend     - DepthEngine abstraction (PyTorch eager / TensorRT)
  depth_filters     - invalid/range/spatial/temporal depth filtering
  ground_filter     - expected-ground-depth filtering via GroundExtrinsics
  roi               - normalized danger ROI polygon (+ physical corridor)
  obstacles         - connected-component obstacle candidates + robust distance
  temporal          - EMA + persistence voting
  velocity_ttc      - closing velocity (lin-reg) and TTC
  risk_engine       - SAFE / WARNING / DANGER / SYSTEM_ERROR state machine
  alarm             - software sound interface
  logger            - CSV telemetry
  recorder          - DANGER screenshots
  renderer          - industrial single-window UI (1280x720 canvas)
  calibration_ui    - interactive calibration (trackbars + ROI drag)
"""