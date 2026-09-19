# Phase 10.0 — Baseline Protection & Architecture Audit

Date: 2025-09-01 · Branch: `feat/3d-click-track` · Tag: `baseline-efficienttam-working`

## Status: PASS — ready for Phase 10A

## 1. Current Stable Baseline

| Item | Value |
|---|---|
| Repo | `/home/seeed/workspace/seg_demo` (note: `~/workspace/object_3d` is an empty dir, NOT the repo) |
| Branch | `main`, clean tree, up to date with origin |
| HW/SW | Jetson AGX Orin 64GB · JetPack 6.2.1 · CUDA 12.6 · SM87 |
| Python | 3.10.12 venv `.venv` (`include-system-site-packages = false`) |
| torch / torchvision | 2.11.0 / 0.26.0 (Jetson AI Lab wheels, cu126) — cuda.is_available = True |
| Model | EfficientTAM Tiny 512 (`checkpoints/efficienttam_ti_512x512.pt`, 17.87M params), eager CUDA, compile off |
| Other deps | opencv-python 5.0.0.93, numpy 2.2.6, hydra-core 1.3.6, iopath 0.1.10, huggingface_hub 1.29.0, nvidia-cudss-cu12 0.8.0.10 (+ preload .pth) |
| Perf baseline | ~15 FPS online BF16 (benchmark docs in `benchmarks/`) |
| pip snapshot | `logs/pip-freeze-baseline.txt` |

### Regression Suite (created this phase) — ALL PASS

```
tests/regression/test_model_load.py          PASS
tests/regression/test_image_prompt.py        PASS
tests/regression/test_video_propagation.py   PASS  (30/30 frames, median area 0.72%)
scripts/run_regression.sh                    one-command runner
```

Committed: `01252fe test: add EfficientTAM regression suite` → tagged `baseline-efficienttam-working` → branch `feat/3d-click-track`.

## 2. Files that MUST NOT be broken

- `app/tracker.py` (OnlineEfficientTAMTracker adapter + LiveFrameStore bounded memory)
- `app/camera_demo.py`, `app/video_demo.py`, `app/image_demo.py`
- `benchmarks/benchmark_video.py`, `tests/test_model_load.py`
- `scripts/setup.sh`, `scripts/env.sh`, `scripts/cudss_preload.*` (cuDSS workaround)
- `checkpoints/efficienttam_ti_512x512.pt`, `third_party/EfficientTAM` (upstream, no patches so far)
- torch / torchvision / cuDSS state in `.venv` — frozen.

## 3. Files that can be extended

- `app/` — additive only: new `depth/`, `geometry/`, `tracking3d/`, `bev/` packages + `camera_demo_3d.py`
- `scripts/` — new runner scripts (e.g. `run_camera_3d.sh`)
- `tests/regression/` — suite runner already supports adding files
- `configs/` (new top-level dir) — `3d_pipeline.yaml`
- `benchmarks/` — new `benchmark_3d_pipeline.py`, `results_3d.md`
- `README.md` — new "3D Click-to-Track" section (append only)

## 4. Proposed new files (Phase 10A+)

```
app/depth/{__init__,estimator,preprocessing,visualization}.py
app/geometry/{__init__,camera_model,depth_sampling,projection}.py
app/tracking3d/{__init__,kalman,trajectory,filters}.py
app/bev/{__init__,renderer}.py
app/camera_demo_3d.py
configs/3d_pipeline.yaml
benchmarks/benchmark_3d_pipeline.py
scripts/run_camera_3d.sh
docs/3d_click_track/   (this audit + per-phase reports)
```

Key integration points already present in `app/tracker.py`:
- masks come out as **bool H×W at camera (video) resolution** from `add_point` / `track_next` → depth sampling can index camera-space depth directly once sizes match.
- EfficientTAM space = 512×512 internally; depth model input will be its own size (518). All geometry must be done in **camera space**; only final XYZ leaves GPU.

## 5. Dependency impact analysis (Depth Anything V2)

- Required new package: **`timm`** (only hard extra dep for DAV2). It depends on torch/torchvision — both already satisfied at pinned versions, so pip will NOT touch them. `pip freeze` before/after per §71.
- No new CUDA wheels, no triton, no ONNX/TRT yet (PyTorch eager first, §14).
- Weights: HuggingFace `depth-anything/Depth-Anything-V2-Metric-*-Small` via `huggingface_hub` (already installed). Store under `checkpoints/` (gitignored, same convention as EfficientTAM ckpt).
- Fallback if DAV2 code pulls unwanted deps: vendor the small model file (~1 file, DINOv2-ViT + DPT head) or isolate via subprocess (§68). No EfficientTAM env change allowed either way.

## 6. Depth model candidate

| Candidate | Type | Size | Why |
|---|---|---|---|
| **Depth Anything V2 Metric — Small (Indoor)** | metric, meters | ~24.8M, 518 input | primary: real-time on Orin, metric scale, warehouse/indoor scenes (forklift, boxes) |
| DAV2 Metric — Small (Outdoor) | metric | same | alternative if scene is outdoor/dock |
| DAV2 Relative — Small | relative | — | rejected as primary (§13/§81: must output meters or label non-metric) |

Decision: **DAV2-Small Metric Indoor**, PyTorch eager FP32→BF16/FP16 autocast, validate metric sanity in Phase 10A/10B before any TRT.

## 7. Integration architecture (incremental)

```
camera frame (BGR, camera space HxW)
  ├─ OnlineEfficientTAMTracker.track_next  → bool mask @ camera res   [existing, untouched]
  └─ MetricDepthEstimator.predict          → depth_m HxW float32      [new, Phase 10A]
        ↓ depth_sampling (erode → valid filter → percentile trim → median)   [10B]
        ↓ camera_model K (yaml, scale-aware for resized inputs)               [10C]
        ↓ projection pixel+Z → XYZ (camera frame: +X right, +Y down, +Z fwd) [10D]
        ↓ gating + Kalman (X,Y,Z,Vx,Vy,Vz; raw vs filtered kept separate)    [10E]
        ↓ trajectory deque(maxlen=N, timestamped) + velocity via Δt           [10F]
        ↓ BEV renderer (pure cv2, top-down, x/z ranges, grid)                 [10G]
        ↓ camera_demo_3d.py serial loop first; async depth later              [10H]
```

Rules: EfficientTAM unchanged (§4/§74) · no bbox-center single-pixel depth (§18) · bounded buffers (§55/§62) · invalid depth ⇒ hold last state + lower confidence, never Z=0 (§58) · depth outlier gating before Kalman (§60).

## 8. Regression plan

- `scripts/run_regression.sh` before EVERY phase commit (Gate: FAIL → stop, fix first).
- Phase 10A+ adds module unit tests; Phase 10I adds `benchmark_3d_pipeline.py` (per-module latency P50/P95, CUDA mem, RSS); Phase 10J 30-min stability soak.

## Next

Phase 10A — Metric Depth Standalone: install `timm` only (with pip freeze snapshots), fetch DAV2-Metric-Indoor-Small, implement `app/depth/estimator.py`, sanity-check metric scale (near < far), save `results/depth_map.png` + min/max/median report.
