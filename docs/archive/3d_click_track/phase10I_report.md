# Phase 10I — Performance Benchmark & Scheduling

Date: 2025-09-01 · Branch: `feat/3d-click-track`

```
Phase:    10I
Status:   PASS
Goal:     Measured (synced GPU timing) per-module cost; pick scheduling that
          reaches >=10 FPS (Good >=12) without touching the stable core.
```

## Methodology

`torch.cuda.synchronize()` around every timed GPU stage (§47/§80). 60 frames
dog.mp4 1280x720. Tables in `benchmarks/results_3d.md` (fp32 track) and
`benchmarks/results_3d_bf16.md` (bf16 autocast track).

## Key measured numbers (P50 ms)

| stage | fp32 track | bf16 track |
|---|---|---|
| track (EfficientTAM online) | 59.4 | 72.1 |
| depth (DAV2 fp16) | 28.9-34.9 | 32-40 |
| mask sampling (GPU) | 4.0 | 4.4 |
| xyz+kalman+bev (CPU) | ~5.6 | ~5.7 |
| full e2e, depth_every=1 | 109.8 (9.1 FPS) | 114.6 (8.7 FPS) |
| full e2e, depth_every=2 | 81.4 (12.3 FPS) | 89.4 (11.2 FPS) |
| full e2e, depth_every=3 | 76.6 (13.0 FPS) | 84.0 (11.9 FPS) |

## Decisions (measured, not assumed)

1. **depth.frequency = 2** as default: 12.3 FPS >= 12 (Good). Depth updates at
   ~6-7 Hz are interpolated by the Kalman between updates; distance lag
   acceptable for walking-speed targets (§37/§51-P2).
2. **EfficientTAM ONLINE dtype = fp32** in the 3D pipeline: bf16 autocast is
   SLOWER in online single-frame propagation (72 vs 59 ms). The historical
   "BF16 ~15 FPS" baseline was offline video mode; existing demos keep their
   own defaults unchanged (§3 escape clause: benchmark proved otherwise).
3. No TensorRT yet (§14/§52): full pipeline already >=12 FPS; revisit only if
   camera live shows a deficit. Optimization ladder §51 stops at P2.
4. Async depth worker (§36) not needed at freq=2; would add correctness risk
   for ~2 FPS. Documented as future option.

## Memory

cuda_alloc 0.25 GB additional (depth) · RSS ~1.9-2.2 GB (models+frames).
30-min stability soak = Phase 10J.

## Regression

EfficientTAM baseline: PASS

## Next

Phase 10J — 30 min soak: RSS/CUDA/FPS/temperature slope checks (§56).
