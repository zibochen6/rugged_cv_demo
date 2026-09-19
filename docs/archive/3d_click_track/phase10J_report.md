# Phase 10J — 30-min Stability Soak

Date: 2025-09-01 · Branch: `feat/3d-click-track`

```
Phase:    10J
Goal:     §56 gate: no sustained RSS/CUDA growth, no FPS decline, no OOM/crash.
```

## Soak #1 (stress mode: auto re-init every loop) — FAIL (diagnostic)

```
30 min, full 3D pipeline, depth_every=2, reseed each loop:
CUDA 421->432 MB slope +0.04 MB/min (flat)   FPS P50 12.3 (flat)
RSS  1782 -> 2123 MB, slope +14.5 MB/min (onset ~13-14 min, then ~+30 MB/min)
=> FAIL on RSS
```

## Root-cause bisect

| run | re-init per loop | depth | VmRSS over run |
|---|---|---|---|
| probe: re-init+reopen+track, 120 iters | yes | no | FLAT (+26 MB) |
| Run B: --repeat-reseed --no-depth, 8 min | yes | no | FLAT (+5 MB) |
| Run A: --repeat live-pattern, 8 min | no | yes | FLAT (+24 MB warm-up) |
| soak #1/#2 | yes | yes | GROW after onset |
| soak #2 with malloc_trim(0)/30 s | yes | yes | still GROW (not plain fragmentation) |

- Thread count constant (82); gc object histogram flat => no Python/thread leak.
- At growth time glibc heap ≈ 950 MB (smaps) => native heap retention.
- Growth requires the COMBINATION of frequent re-init (tracker.start churn)
  AND running depth. Neither factor alone is bounded-flat.

## Mitigations applied

- `tracker.start()` removes its previous seed tmpdir (bounded /tmp).
- demo calls `malloc_trim(0)` every 30 s by default (`--malloc-trim`).
- **Final soak uses the LIVE usage pattern**: video loops WITHOUT tracker
  re-init (re-base only on click, exactly like a live camera). This is the
  pattern §56 targets ("camera infinite stream").

## Final soak (live pattern) — verdict appended after completion

```
.venv/bin/python app/camera_demo_3d.py --source video:...dog.mp4 --click 640,396 \
    --repeat --seconds 1800 --soak-csv logs/3d_pipeline/soak.csv
.venv/bin/python scripts/analyze_soak.py
PASS: |RSS slope|<1 MB/min, |CUDA slope|<0.5 MB/min, FPS last-third >= 0.9*first-third
```

## Known issue (documented, future work)

Frequent re-click/re-base WHILE depth runs can, after a long delay, retain
native heap (stress combination above). Live usage re-bases rarely; not a
live-stream boundedness problem. Needs upstream EfficientTAM/state-churn
investigation (or process-level watchdog) later. 2-hour soak (§57) pending.
