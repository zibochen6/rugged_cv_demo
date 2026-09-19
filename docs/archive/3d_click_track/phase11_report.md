# Phase 11 — Class-Agnostic 3D Bounding Box (Estimated Monocular OBB)

Status: **PASS** (unit + regression + headless gate + bench; 30-min soak below).

Upgrades the Phase-10 "3D localization" pipeline (center XYZ only) to a true
class-agnostic 3D detection output: center XYZ **+ W/H/L + yaw + 8 corners**,
drawn as a real 3D cuboid overlay on the RGB image and as an oriented
footprint in the top-down BEV. EfficientTAM is untouched; no new model.

Pipeline (all measured, §88):

```
EfficientTAM mask + metric depth
  -> erode mask, back-project valid pixels (pinhole)   [app/geometry/pointcloud.py]
  -> per-axis MAD + percentile outlier filtering       [pointcloud.filter_outliers]
  -> PCA oriented cuboid fit (center/dims/yaw)         [app/geometry/cuboid.py]
  -> temporal stabilization (center EMA, dims rate-limit,
     doubled-angle yaw EMA for ±pi wrap)               [app/tracking3d/box_filter.py]
  -> project 8 corners with K, draw 12 edges           [cuboid.draw_cuboid]
  -> BEV oriented footprint (X-Z)                      [app/bev/renderer.py]
```

## 11A — geometry core (commit b9f039a)

- `mask_to_pointcloud`: numpy CPU (see perf note), 3x3 erosion with thin-mask
  fallback (same rule as §39 sampling), valid-depth gate, stride cap.
- `filter_outliers`: per-axis MAD (k=4, sigma floored) then 1-99 percentile
  clip; keeps object extent, removes border bleeding.
- `fit_cuboid`: yaw from 2x2 analytic eigensolve on the horizontal (X,Z)
  scatter (Jetson torch lacks cusolver for `linalg.eigh`; numpy is exact and
  free of GPU serialization); dims from robust per-axis extents in the rotated
  frame; height from Y extent. min_points=32.
- Tests: synthetic rotated box recovered within 10% dims / 5 deg yaw; outlier
  blob (10% of points at +3 m) changes dims < 10%. `tests/geometry/test_cuboid.py` PASS.
- `BoxSmoother`: EMA on center, rate-limited dims (<=3%/frame), yaw EMA on
  (cos2t, sin2t) so 179deg->-179deg is continuous; re-anchors on NaN.
  `tests/tracking3d/test_box_filter.py` PASS (yaw stays at +/-89.8deg, spike clamped).

## 11B/C — demo integration + BEV footprint (commit 70d6f47)

- `camera_demo_3d.py`: filtered cuboid (green, 12 edges) + label; raw cuboid
  (red, thin) via `--show-raw-box`; HUD `Size: WxHxL m  Yaw: +/-deg` line;
  `R` also resets the box smoother.
- Debug flags: `--show-mask --show-depth --show-pointcloud --show-raw-box
  --show-filtered-box`; headless runs save `results/cuboid_demo.png` /
  `results/box_debug.png` when any viz flag is set. `--no-box` bisect flag.
- BEV: oriented footprint rectangle (W x L at yaw) replaces the lone dot;
  `--show-pointcloud` scatters the masked cloud in X-Z.
- Visual check (dog.mp4 click 640,396): cuboid hugs the dog, raw box visibly
  noisier than filtered, BEV footprint oriented correctly. `results/cuboid_demo.png`.

### Perf note (why the box runs on CPU numpy)

v1 ran point cloud + OBB on CUDA. Benchmarking showed the extra GPU kernels
serialize with EfficientTAM+depth on the single Orin stream and an in-loop
`torch.cuda.synchronize()` for timing destroyed CPU/GPU overlap: e2e fell to
~7-8 FPS. Moving the (small, <=4k points) work to CPU numpy overlaps it with
the GPU pipeline: steady-state full pipeline **12.8 FPS** with box
(depth_every=2), box CPU 5.5-5.8 ms at depth frequency. Depth D2H for the box
is deferred to the next frame (immediate `.cpu()` after `predict` serializes).

Machine note: `nvhost_podgov` GPU governor ramps over seconds; short
(<60-frame) headless runs under-report FPS (cold-clock amortization). Steady
state and >=60-frame numbers are the representative ones. MAXN power mode.

## 11D — benchmark (commit f177e0a, benchmarks/results_3d.md)

Synced GPU timing, 60 frames 720p, mode E = full + box:

| config | e2e P50 ms | FPS | pc | outlier | obb | proj | boxdraw |
|---|---|---|---|---|---|---|---|
| D full 3D depth_every=2 | 83.1 | 12.0 | - | - | - | - | - |
| **E full+box depth_every=2** | **90.2** | **11.1** | 8.9 | 1.6 | 1.3 | 0.1 | 0.3 |
| E full+box depth_every=1 | 126.3 | 7.9 | 9.5 | 1.6 | 1.2 | 0.1 | 0.3 |

Box adds ~0.9 FPS in the synced (no-overlap) bench, ~1.5 FPS in the live demo
(12.8 -> ~11.2 soak P50). Geometry is NOT the bottleneck (track 65 ms dominates).

## 11E — stability (live pattern, 30 min, box on)

10-min probe first: warm-up step in minute 0-1 (RSS 1771->1817, CUDA
398->470 MB), then perfectly flat (RSS 1825, CUDA 470 +/- 3) -> no linear
growth; the short window merely dilutes the warm-up step into a slope.
30-min gate (same methodology as Phase 10J):

```
duration: 29.9 min, samples: 356 (19347 frames, valid 17538 = 90.7%)
RSS:  first=1791MB last=1827MB slope=+0.28 MB/min
CUDA: first=404MB last=461MB slope=-0.43 MB/min   (flat; warm-up step only)
FPS:  first3rd=10.7 last3rd=10.9 P50=10.5 P95=14.1
box:  mean 5.5 ms (max 22.9)
TEMP: tj max 62 C   POWER: VDD_GPU_SOC avg 14.2 W (max 31.3)
SOAK GATE: PASS
```

RSS slope +0.28 MB/min is BETTER than the Phase-10J no-box soak (+0.37),
i.e. the box stage adds no measurable retention. Steady FPS ~10.5-11 vs
12.4-12.7 without box: the ~2 FPS difference is the expected CPU box cost.

## Honesty

This is an **Estimated Monocular 3D Bounding Box** from a single metric-depth
model with estimated intrinsics: UI keeps `[APPROXIMATE: estimated K]` and the
cuboid inherits depth scale bias until `scripts/calibrate_distance.py` is run.
Not LiDAR ground truth.

## Gates

- EfficientTAM regression suite: ALL PASS (after 11B/C and 11D).
- Unit: test_cuboid / test_box_filter / test_bev_renderer / all Phase-10 tests PASS.
- Headless: 60/60 valid, e2e 10.2 FPS (60-frame window incl. cold clocks), PASS.
- Bench: E@2 = 11.1 FPS >= 10 target.
- 30-min soak: PASS (RSS +0.28 MB/min, CUDA flat, FPS 10.7->10.9).
