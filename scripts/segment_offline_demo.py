#!/usr/bin/env python3
"""Offline acceptance: REAL EfficientTAM on synthetic videos, no camera.

Scene A (default, --scene a): a bright ball sweeps across the frame, fully
leaves the field of view for ~2.4 s, then comes back and leaves again. We
click it once at the start and let the real SegmentService track it,
recording state + overlay frames.

Scene B (--scene b, swap object): the same ball is replaced by a clearly
different object (grey rounded square ~150, similar size, on the ball's
trajectory) while the tracker is LOST. The identity verification gate must
keep the tracker LOST during the swap window and only resume once the real
ball comes back.

Expected timeline (scene A):
    tracking -> (ball gone) -> lost with ghost -> (ball back) -> tracking

Evidence is saved to
    <repo>/frontend/dist/debug/segment_offline/         (scene A)
    <repo>/frontend/dist/debug/segment_offline_swap/    (scene B)
(served by the studio at /debug/segment_offline/...) plus a states.jsonl.

Run on the Jetson, from the repo root:
    .venv/bin/python scripts/segment_offline_demo.py --scene a
    .venv/bin/python scripts/segment_offline_demo.py --scene b
    .venv/bin/python scripts/segment_offline_demo.py --scene all
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.segment.service import SegmentService  # noqa: E402

W, H = 640, 360
R = 28            # ball radius
Y = H // 2
SWEEP_START = 60  # first-frame center x
SWEEP_END = W + 2 * R  # fully out to the right
GAP1 = (180, 240)      # scene A/B first gap (off-screen) [i0, i1)
SWEEP2 = (240, 420)    # scene A second sweep
TAIL_END = 460


def sweep_x(i: int, t0: int, t1: int) -> float:
    """Ball x for a sweep starting at frame t0 and reaching off-screen at t1."""
    return SWEEP_START + (i - t0) * (SWEEP_END - SWEEP_START) / (t1 - t0)


def ball_x(i: int) -> float:
    if GAP1[0] <= i < GAP1[1]:
        return SWEEP_END + 10.0  # off-screen
    if i < GAP1[0]:
        return sweep_x(i, 0, GAP1[0])
    if i < SWEEP2[1]:
        return sweep_x(i, SWEEP2[0], SWEEP2[1])
    return SWEEP_END + 10.0


# scene B timeline
SWAP_IDX = (240, 330)   # replacement object present
GAP2 = (330, 360)       # everything off-screen
SWEEP3 = (360, 460)     # original ball comes back


def swap_x(i: int) -> float:
    if SWAP_IDX[0] <= i < SWAP_IDX[1]:
        return sweep_x(i, SWAP_IDX[0], SWAP_IDX[1])
    return SWEEP_END + 10.0


def render_noise(i: int) -> np.ndarray:
    rng = np.random.default_rng(42 + i)
    frame = rng.integers(60, 110, (H, W, 3), dtype=np.uint8)
    # static clutter for tracking context
    for cx, cy, cr in ((500, 60, 24), (90, 320, 18), (330, 40, 12), (40, 60, 10), (600, 300, 16)):
        cv2.circle(frame, (cx, cy), cr, (110, 105, 85), -1)
    return frame


def render_frame(i: int) -> np.ndarray:
    frame = render_noise(i)
    x = ball_x(i)
    if 0 <= x <= W:
        # bright white ball: strong grey-domain contrast so the template
        # re-lock can find it after it reappears
        cv2.circle(frame, (int(x), Y), R, (255, 255, 255), -1)
        cv2.circle(frame, (int(x), Y), R - 6, (200, 210, 230), 2)
    return frame


def render_frame_swap(i: int) -> np.ndarray:
    frame = render_noise(i)
    if SWAP_IDX[0] <= i < SWAP_IDX[1]:
        # replacement object: grey (~150) rounded square, similar size to
        # the ball, travelling the ball's trajectory
        s = 52
        x0, y0 = int(swap_x(i)) - s // 2, Y - s // 2
        cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), (150, 150, 150), -1)
        cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), (125, 125, 125), 2)
    else:
        x = ball_x(i)
        if 0 <= x <= W:
            cv2.circle(frame, (int(x), Y), R, (255, 255, 255), -1)
            cv2.circle(frame, (int(x), Y), R - 6, (200, 210, 230), 2)
    return frame


class VideoCamera:
    def __init__(self, frames):
        self._frames = frames
        self._i = 0

    def is_running(self):
        return True

    def get_latest_frame(self):
        idx = min(self._i, len(self._frames) - 1)
        self._i += 1
        return idx + 1, self._frames[idx]


def run_scene(scene: str, out_dir: Path, max_frames: int) -> int:
    render = render_frame if scene == "a" else render_frame_swap
    frames = [render(i) for i in range(max_frames)]
    cam = VideoCamera(frames)
    svc = SegmentService(camera=cam, max_side=640, threaded=False)

    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "states.jsonl"
    log = open(log_path, "w")

    def record(i: int, note: str = ""):
        st = svc.status()
        line = {
            "frame": i,
            "note": note,
            "state": st["state"],
            "mask_area": st.get("mask_area"),
            "has_target": st["has_target"],
            "camera_running": st["camera_running"],
            "model_fps": st["model_fps"],
            "infer_ms": st["infer_ms"],
            "error": st["error"],
            "target_quality": st.get("target_quality"),
            "relock_note": st.get("relock_note"),
            "resume_armed": st.get("resume_armed"),
        }
        log.write(json.dumps(line) + "\n")
        log.flush()
        return st

    print("[click2seg offline] scene=%s starting real EfficientTAM model..." % scene)
    t0 = time.time()
    st = svc.start()
    print("[click2seg offline] start ->", st)
    if not st.get("ok"):
        print("[click2seg offline] FAIL: cannot start:", st)
        return 1

    # load the model (threaded=False -> explicit synchronous load)
    res = svc.ensure_model()
    if not res.get("ok"):
        print("[click2seg offline] FAIL: model error:", res)
        return 1
    print("[click2seg offline] model ready (%.1fs), clicking ball at first frame"
          % (time.time() - t0))

    # first click = select target at the ball on frame 0
    res = svc.select_target(SWEEP_START / W, Y / H)
    print("[click2seg offline] select ->", res)
    if not res.get("ok"):
        print("[click2seg offline] FAIL: select failed:", res)
        return 1

    timeline = []  # (state, first_frame, last_frame)
    last_state = None
    timeline_start = 0
    states_by_frame = {}

    def note_state(state, i):
        nonlocal last_state, timeline_start
        states_by_frame[i] = state
        if state != last_state:
            if last_state is not None:
                timeline.append((last_state, timeline_start, i - 1))
            last_state = state
            timeline_start = i

    for i in range(1, max_frames):
        svc.process_one()
        st = record(i)
        note_state(st["state"], i)
        if i % 25 == 0 or i in (1, max_frames - 1):
            try:
                st2 = svc.status()
                canvas = cv2.resize(frames[i], (1280, 720), interpolation=cv2.INTER_NEAREST)
                for poly in st2["polygons"]:
                    pts = np.array(poly, dtype=np.int32).reshape(-1, 1, 2)
                    cv2.fillPoly(canvas, [pts], (0, 180, 60))
                    cv2.polylines(canvas, [pts], True, (120, 255, 160), 2)
                for poly in st2["ghost_polygons"]:
                    pts = np.array(poly, dtype=np.int32).reshape(-1, 1, 2)
                    cv2.polylines(canvas, [pts], True, (220, 220, 220), 2)
                for px, py, _label in st2["points"]:
                    cv2.circle(canvas, (int(px * 1280), int(py * 720)), 8, (255, 255, 0), -1)
                cv2.putText(canvas, "scene %s frame %d state=%s fps=%s"
                            % (scene, i, st2["state"], st2["model_fps"]),
                            (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 4)
                cv2.putText(canvas, "scene %s frame %d state=%s fps=%s"
                            % (scene, i, st2["state"], st2["model_fps"]),
                            (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                cv2.imwrite(str(out_dir / ("frame_%04d.jpg" % i)), canvas)
            except Exception as exc:  # pragma: no cover
                print("[click2seg offline] overlay save failed:", exc, flush=True)

    if last_state is not None:
        timeline.append((last_state, timeline_start, max_frames - 1))
    log.close()

    print("\n[click2seg offline] scene=%s timeline: %s" % (scene, timeline))
    if scene == "a":
        return verdict_scene_a(timeline, out_dir)
    return verdict_scene_b(states_by_frame, timeline, out_dir)


def verdict_scene_a(timeline, out_dir) -> int:
    states_seen = [s for s, _, _ in timeline]
    got_lost = "lost" in states_seen
    first_lost = states_seen.index("lost") if got_lost else -1
    got_recover = got_lost and "tracking" in states_seen[first_lost + 1:]
    print("[click2seg offline] scene A PASS: lost -> ghost -> auto resume observed" if (got_lost and got_recover)
          else "[click2seg offline] scene A FAIL: expected tracking -> lost -> tracking, got %s" % timeline)
    return 0 if (got_lost and got_recover) else 2


def verdict_scene_b(states_by_frame, timeline, out_dir) -> int:
    states_seen = [s for s, _, _ in timeline]
    got_lost = "lost" in states_seen
    first_lost = states_seen.index("lost") if got_lost else -1
    # the swap window (240..329 inclusive) must never show tracking
    swap_clean = True
    for f in range(SWAP_IDX[0], SWAP_IDX[1]):
        if states_by_frame.get(f) == "tracking":
            swap_clean = False
            break
    # the original target must be tracked again from >= 360
    recovered_after_swap = any(
        states_by_frame.get(f) == "tracking" for f in range(SWEEP3[0], max(states_by_frame) + 1)
    )
    passed = got_lost and swap_clean and recovered_after_swap
    if passed:
        print("[click2seg offline] scene B PASS: swap object rejected (lost during %d-%d), "
              "ball re-locked after %d" % (SWAP_IDX[0], SWAP_IDX[1] - 1, SWEEP3[0]))
    else:
        print("[click2seg offline] scene B FAIL: got_lost=%s swap_clean=%s recovered_after_swap=%s "
              "timeline=%s" % (got_lost, swap_clean, recovered_after_swap, timeline))
    return 0 if passed else 2


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scene", choices=["a", "b", "all"], default="all")
    ap.add_argument("--out", default=None,
                    help="overwrite the default output dir (debug/segment_offline[_swap])")
    ap.add_argument("--max-frames", type=int, default=TAIL_END)
    args = ap.parse_args()

    scenes = ["a", "b"] if args.scene == "all" else [args.scene]
    rc = 0
    for scene in scenes:
        out_dir = Path(args.out) if args.out else (
            ROOT / "frontend/dist/debug/segment_offline_swap" if scene == "b"
            else ROOT / "frontend/dist/debug/segment_offline")
        code = run_scene(scene, out_dir, args.max_frames)
        rc = max(rc, code)
        print("[click2seg offline] scene %s -> %s" % (scene, "PASS" if code == 0 else "FAIL"))
    return rc


if __name__ == "__main__":
    sys.exit(main())