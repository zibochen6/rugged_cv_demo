"""SegmentService behaviour tests with a fake model + fake camera.

Covers: start gating, click-to-target, tracking, lost->ghost, auto-resume,
mask-quality gate, identity-verified auto re-lock (accept + reject),
the LOST resume identity gate, clear, stop and the interaction guards.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import cv2
import unittest
from unittest import mock

from backend.app.segment.service import SegmentService


class FakeCamera:
    def __init__(self, running=True, size=(360, 640)):
        self._running = running
        self._size = size
        self._frame_id = 1000
        self.frame = np.zeros((size[0], size[1], 3), dtype=np.uint8)
        # bright square the template re-lock can find
        self.frame[100:130, 100:130] = 255

    def is_running(self):
        return self._running

    def get_latest_frame(self):
        self._frame_id += 1
        return self._frame_id, self.frame


class FakeModel:
    """Scripted model: masks come from a per-call queue."""

    def __init__(self, frames=None):
        self.frames = list(frames) if frames is not None else []
        self.track_calls = 0
        self.select_calls = 0
        self.add_calls = 0
        self.last_points = []
        self.loaded = False

    def load(self):
        self.loaded = True

    def select(self, img, x, y):
        self.select_calls += 1
        self.last_points.append(("select", x, y))
        return self._next()

    def add_point(self, img, x, y, label):
        self.add_calls += 1
        self.last_points.append(("add", x, y, label))
        return self._next()

    def track(self, img):
        self.track_calls += 1
        return self._next()

    def reset(self):
        pass

    def _next(self):
        if self.frames:
            return self.frames.pop(0)
        return np.zeros((360, 640), dtype=bool)


def present_mask():
    """Small enough to be quality-ok, and covers the FakeCamera bright
    square (100:130) so the identity template built from it is matchable."""
    m = np.zeros((360, 640), dtype=bool)
    m[80:280, 80:280] = True
    return m


def absent_mask():
    return np.zeros((360, 640), dtype=bool)


def foreign_mask():
    """Big mask elsewhere; its grayscale crop on the fake frame is CONSTANT
    (no bright square inside) -> identity verification must fail."""
    m = np.zeros((360, 640), dtype=bool)
    m[20:220, 480:620] = True
    return m


class TestSegmentService(unittest.TestCase):
    def make_service(self, model, camera=None, **kw):
        cam = camera or FakeCamera()
        return SegmentService(model_factory=lambda: model, camera=cam, max_side=640, threaded=False)

    def start_ready(self, svc):
        """start() + the model load the background loop would perform."""
        res = svc.start()
        self.assertTrue(res["ok"])
        svc._load_model_once()
        st = svc.status()
        self.assertFalse(st["loading"], st)
        self.assertIsNone(st["error"], st)

    # ------------------------------------------------------------- gating
    def test_click_before_start_rejected(self):
        svc = self.make_service(FakeModel())
        res = svc.select_target(0.5, 0.5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "SEGMENT_NOT_STARTED")

    def test_start_requires_camera(self):
        svc = self.make_service(FakeModel(), camera=FakeCamera(running=False))
        res = svc.start()
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "SEGMENT_CAMERA_NOT_RUNNING")

    def test_start_ok_and_status(self):
        svc = self.make_service(FakeModel())
        res = svc.start()
        self.assertTrue(res["ok"])
        st = svc.status()
        self.assertTrue(st["enabled"])
        self.assertEqual(st["state"], "idle")

    # ------------------------------------------------------------ clicks
    def test_select_creates_target(self):
        model = FakeModel(frames=[present_mask()])
        svc = self.make_service(model)
        self.start_ready(svc)
        res = svc.select_target(0.5, 0.5)
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "tracking")
        st = svc.status()
        self.assertTrue(st["has_target"])
        self.assertEqual(st["state"], "tracking")
        self.assertEqual(len(st["polygons"]), 1)
        self.assertEqual(model.select_calls, 1)

    def test_model_fps_reports_completed_inference_cadence(self):
        model = FakeModel(frames=[present_mask()] * 3)
        svc = self.make_service(model)
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        with mock.patch(
            "backend.app.segment.service.time.monotonic",
            side_effect=[10.0, 10.25],
        ):
            svc.process_one()
            svc.process_one()
        self.assertEqual(svc.status()["model_fps"], 4.0)

    def test_select_invalid_coords(self):
        svc = self.make_service(FakeModel())
        svc.start()
        res = svc.select_target(1.5, 0.5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "SEGMENT_INVALID_COORDINATES")

    def test_add_point_refines(self):
        model = FakeModel(frames=[present_mask(), present_mask()])
        svc = self.make_service(model)
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        res = svc.add_point(0.6, 0.6)
        self.assertTrue(res["ok"])
        self.assertEqual(model.add_calls, 1)
        self.assertEqual(len(svc.status()["points"]), 2)

    def test_add_point_before_target(self):
        svc = self.make_service(FakeModel())
        svc.start()
        res = svc.add_point(0.5, 0.5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "SEGMENT_NO_TARGET")

    # --------------------------------------------------------------- loss
    def test_lost_ghost_and_auto_resume(self):
        # one present (select), 6 absent (lost), then 3 present (resume)
        # template re-lock disabled here: this test isolates the hysteresis
        seq = [present_mask()] + [absent_mask()] * 6 + [present_mask()] * 3
        model = FakeModel(frames=seq)
        svc = self.make_service(model)
        svc._reseed_threshold = 2.0  # impossible score: template re-lock off
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        st = svc.status()
        self.assertEqual(st["state"], "tracking")
        # absent frames
        for _ in range(4):
            svc.process_one()
        self.assertEqual(svc.status()["state"], "tracking")  # hysteresis not yet
        svc.process_one()  # 5th absent
        svc.process_one()  # 6th absent -> lost
        st = svc.status()
        self.assertEqual(st["state"], "lost")
        self.assertGreaterEqual(len(st["ghost_polygons"]), 1)  # ghost kept
        self.assertEqual(st["polygons"], [])
        # first present frame: still lost (needs 2)
        svc.process_one()
        self.assertEqual(svc.status()["state"], "lost")
        # second present frame: recovered
        svc.process_one()
        st = svc.status()
        self.assertEqual(st["state"], "tracking")
        self.assertGreaterEqual(len(st["polygons"]), 1)
        self.assertEqual(st["ghost_polygons"], [])

    def test_clear_returns_idle(self):
        model = FakeModel(frames=[present_mask()])
        svc = self.make_service(model)
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        res = svc.clear_target()
        self.assertTrue(res["ok"])
        st = svc.status()
        self.assertFalse(st["has_target"])
        self.assertEqual(st["state"], "idle")
        self.assertEqual(st["polygons"], [])

    def test_stop_resets(self):
        model = FakeModel(frames=[present_mask()])
        svc = self.make_service(model)
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        svc.stop()
        st = svc.status()
        self.assertFalse(st["enabled"])
        self.assertFalse(st["has_target"])
        self.assertEqual(st["state"], "idle")

    def test_too_many_points(self):
        model = FakeModel(frames=[present_mask()] * 20)
        svc = self.make_service(model)
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        last = None
        for _ in range(30):
            last = svc.add_point(0.6, 0.6)
        self.assertFalse(last["ok"])
        self.assertEqual(last["code"], "SEGMENT_TOO_MANY_POINTS")

    def test_model_load_error_surface(self):
        class BoomModel:
            def load(self):
                raise RuntimeError("boom")

        svc = self.make_service(BoomModel())
        svc.start()
        svc._load_model_once()
        st = svc.status()
        self.assertFalse(st["loading"])
        self.assertIn("boom", st["error"])

    # ------------------------------------------- lost re-acquisition
    def test_auto_relock_after_reappear(self):
        # select present; the 6th absent frame flips to LOST and reseed acts
        # immediately on the strong peak by re-selecting at the peak (fresh
        # re-anchor), restoring TRACKING in the same frame.
        # NOTE: the fake frame's template peak scores ~0.64, so the trigger
        # threshold is lowered from the default 0.65 for this unit test.
        seq = ([present_mask()] + [absent_mask()] * 6 + [present_mask()])
        model = FakeModel(frames=seq)
        svc = self.make_service(model)
        svc._reseed_interval_s = 0.0
        svc._reseed_threshold = 0.5
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        self.assertEqual(svc.status()["state"], "tracking")
        with self.assertLogs("backend.app.segment.service", level="INFO") as cm:
            for _ in range(6):
                svc.process_one()
        # verified re-lock re-anchored the target -> back to TRACKING
        self.assertEqual(svc.status()["state"], "tracking")
        self.assertEqual(model.select_calls, 2)   # select_target + re-lock re-select
        self.assertEqual(model.add_calls, 0)
        self.assertTrue(any("auto re-lock at" in line for line in cm.output), cm.output)
        self.assertEqual(len(svc.status()["points"]), 1)   # replaced by re-lock point

    def test_no_relock_without_peak(self):
        # No matching template peak -> reseed is a no-op; state stays LOST.
        seq = [present_mask()] + [absent_mask()] * 6
        model = FakeModel(frames=seq)
        svc = self.make_service(model)
        svc._reseed_interval_s = 0.0
        svc._reseed_threshold = 2.0   # impossible threshold -> never a peak
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        for _ in range(8):
            svc.process_one()
        self.assertEqual(svc.status()["state"], "lost")
        self.assertEqual(model.select_calls, 1)  # only the initial select_target

    def test_quality_gate_poor_vs_ok(self):
        # Full-frame mask -> poor: no identity template, hint set, resume
        # not armed.
        big = np.ones((360, 640), dtype=bool)
        svc = self.make_service(FakeModel(frames=[big]))
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        st = svc.status()
        self.assertEqual(st["state"], "tracking")
        self.assertEqual(st["target_quality"], "poor")
        self.assertIsNotNone(st["target_hint"])
        self.assertFalse(st["resume_armed"])
        self.assertIsNone(svc._template)

        # Small mask -> ok: template built, resume armed, no hint.
        svc2 = self.make_service(FakeModel(frames=[present_mask()]))
        self.start_ready(svc2)
        svc2.select_target(0.5, 0.5)
        st2 = svc2.status()
        self.assertEqual(st2["target_quality"], "ok")
        self.assertIsNone(st2["target_hint"])
        self.assertTrue(st2["resume_armed"])
        self.assertIsNotNone(svc2._template)

    def test_commit_mask_natural_resume(self):
        # While LOST, an incoming (model track() output) mask drives the
        # state machine back to tracking through the usual two-frame
        # hysteresis. Identity is the model's own output (trusted); there
        # is no rigid template re-check on the natural path.
        seq = [present_mask()] + [absent_mask()] * 6
        model = FakeModel(frames=seq)
        svc = self.make_service(model)
        svc._reseed_threshold = 2.0  # disable template reseed
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        for _ in range(6):
            svc.process_one()
        self.assertEqual(svc.status()["state"], "lost")
        gray = cv2.cvtColor(svc._camera.frame, cv2.COLOR_BGR2GRAY)
        # first present frame: still lost (hysteresis needs two frames)
        svc._commit_mask(present_mask(), 10000, 1.0, 1.0, frame_gray=gray)
        self.assertEqual(svc._sm.state, "lost")
        # second present frame: recovered
        svc._commit_mask(present_mask(), 10001, 1.0, 1.0, frame_gray=gray)
        self.assertEqual(svc._sm.state, "tracking")

    def test_clear_and_stop_reset_relock_state(self):
        model = FakeModel(frames=[present_mask()])
        svc = self.make_service(model)
        self.start_ready(svc)
        svc.select_target(0.5, 0.5)
        svc._pending_relock = (0.2, 0.4, 0.9, 1.0)
        svc._suppressed = [[0.2, 0.4, 999999.0]]
        svc._relock_note = "x"
        svc.clear_target()
        st = svc.status()
        self.assertIsNone(svc._pending_relock)
        self.assertEqual(svc._suppressed, [])
        self.assertIsNone(svc._relock_note)
        self.assertIsNone(st["target_quality"])
        self.assertEqual(st["target_hint"], None)
        self.assertFalse(st["resume_armed"])


if __name__ == "__main__":
    unittest.main()
