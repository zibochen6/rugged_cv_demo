# Phase 10A — Metric Depth Standalone

Date: 2025-09-01 · Branch: `feat/3d-click-track`

```
Phase:    10A
Status:   PASS
Goal:     Standalone metric depth: Image -> Depth (m) -> Visualize -> Save PNG
```

## Changed / new files

- `app/depth/__init__.py`, `estimator.py`, `preprocessing.py`, `visualization.py` (new)
- `app/depth_demo.py` (new, standalone demo)
- `tests/depth/test_depth_standalone.py` (new gate)
- `scripts/download_models.sh` (append DAV2 metric download)
- `requirements-jetson.txt` (append transformers, socksio with comments)

## Dependencies changed (§71 snapshots in logs/)

- transformers 5.16.1 (+ tokenizers/safetensors/regex/… all pure Python)
- socksio 1.0.0 (lab SOCKS proxy; httpx requirement)
- **torch 2.11.0 / torchvision 0.26.0 UNCHANGED** (verified by pip freeze diff + regression)

## Model

- `depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf` (HF-transformers format,
  99MB safetensors, Hypersim-fine-tuned => METRIC, meters, indoor).
- IMPORTANT: original `-Metric-Indoor-Small` (non-hf) repo is GONE (401); use `-hf`.
- Preprocessing per shipped `preprocessor_config.json`: keep-aspect-ratio resize
  longest edge 518, multiple of 14, ImageNet norm. Implemented in `app/depth/preprocessing.py`.

## Validation (measured, dog.mp4 frame 0, 1280x720)

```
fp32: min 2.892 m, median 6.281 m, max 19.375 m; 84.6 ms/f (11.8 FPS)
bf16: median 6.277 (drift 0.07%); 56.7 ms/f (17.6 FPS)
fp16: median 6.283 (drift 0.02%); 44.9 ms/f (22.3 FPS)
near/far sanity: lower-center 4.01 m < upper-band 9.42 m  -> OK
results/depth_map*.png: person/dog warm (near), corridor dark blue (far)  OK
```

NOTE: dog.mp4 is an OUTDOOR scene; the INDOOR metric model keeps correct
ordering but absolute scale on outdoor scenes is biased. Authoritative metric
validation = tape-measure with the real indoor camera (§16, later gate).

## Regression

EfficientTAM baseline: PASS (all 3 tests, after dep install)

## Decisions

- Depth dtype default stays fp32 for correctness-first; bf16/fp16 available and
  numerically clean. fp16 currently fastest (22.3 FPS) — depth dtype choice
  deferred to Phase 10I benchmark.
- No TensorRT yet (§14).
- `predict_tensor()` returns CUDA float32 (H,W) so mask-depth sampling (10B)
  stays on GPU (§39).

## Next

Phase 10B — EfficientTAM mask + depth robust sampling (erode -> valid filter ->
percentile trim -> median + confidence metrics).
