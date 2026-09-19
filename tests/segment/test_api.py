"""Segment API endpoint tests (direct handler calls, no TestClient).

The app's starlette/httpx pair is incompatible with FastAPI's TestClient,
so handlers are invoked directly: request-model validation is exercised via
the pydantic model, and error mapping via the raised HTTPException.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import unittest
from unittest import mock

from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.segment.api import (
    ClickRequest,
    segment_clear,
    segment_click,
    segment_start,
    segment_status,
    segment_stop,
)


class StubService:
    def __init__(self, status_payload=None, fail=None):
        self.calls = []
        self._status = status_payload or {
            "ok": True, "enabled": True, "state": "tracking", "has_target": True,
            "loading": False, "error": None, "points": [[0.5, 0.5, 1]],
            "polygons": [[]], "ghost_polygons": [], "center": [640, 360],
            "infer_ms": 120.0, "model_fps": 8.3, "frame_id": 5, "ts": 1.0,
            "camera_running": True,
        }
        self._fail = fail or {}

    def _maybe(self, name):
        self.calls.append(name)
        if name in self._fail:
            return self._fail[name]
        return {"ok": True, "state": "tracking", "message": "ok"}

    def status(self):
        self.calls.append("status")
        return dict(self._status)

    def start(self):
        return self._maybe("start")

    def stop(self):
        return self._maybe("stop")

    def clear_target(self):
        return self._maybe("clear")

    def select_target(self, x, y, label):
        self.calls.append(("select", x, y, label))
        if "select" in self._fail:
            return self._fail["select"]
        return {"ok": True, "state": "tracking", "message": "ok"}

    def add_point(self, x, y, label):
        self.calls.append(("add", x, y, label))
        if "add" in self._fail:
            return self._fail["add"]
        return {"ok": True, "state": "tracking", "message": "ok"}


def with_service(stub):
    return mock.patch("backend.app.segment.api.get_segment_service", return_value=stub)


class TestSegmentAPI(unittest.TestCase):
    def test_status(self):
        stub = StubService()
        with with_service(stub):
            body = segment_status()
        self.assertEqual(body["state"], "tracking")
        self.assertTrue(body["has_target"])
        self.assertIn("model_fps", body)
        self.assertIn("status", stub.calls)

    def test_start_ok(self):
        stub = StubService()
        with with_service(stub):
            res = segment_start()
        self.assertTrue(res["ok"])
        self.assertIn("start", stub.calls)

    def test_start_camera_not_running(self):
        stub = StubService(fail={"start": {
            "ok": False, "code": "SEGMENT_CAMERA_NOT_RUNNING", "message": "no cam",
        }})
        with with_service(stub):
            with self.assertRaises(HTTPException) as cm:
                segment_start()
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "SEGMENT_CAMERA_NOT_RUNNING")

    def test_click_model_rejects_out_of_range(self):
        with self.assertRaises(ValidationError):
            ClickRequest(x=1.5, y=0.5)
        with self.assertRaises(ValidationError):
            ClickRequest(x=0.5, y=-0.1)

    def test_click_selects_when_no_target(self):
        stub = StubService(status_payload={
            "ok": True, "enabled": True, "state": "idle", "has_target": False,
            "loading": False, "error": None, "points": [], "polygons": [],
            "ghost_polygons": [], "center": None, "infer_ms": None,
            "model_fps": None, "frame_id": None, "ts": 0.0, "camera_running": True,
        })
        with with_service(stub):
            res = segment_click(ClickRequest(x=0.5, y=0.5))
        self.assertTrue(res["ok"])
        self.assertTrue(any(c[0] == "select" for c in stub.calls if isinstance(c, tuple)))

    def test_click_adds_when_has_target(self):
        stub = StubService()
        with with_service(stub):
            res = segment_click(ClickRequest(x=0.4, y=0.4))
        self.assertTrue(res["ok"])
        self.assertTrue(any(c[0] == "add" for c in stub.calls if isinstance(c, tuple)))

    def test_click_loading_maps_to_409(self):
        stub = StubService(status_payload={
            "ok": True, "enabled": True, "state": "idle", "has_target": False,
            "loading": True, "error": None, "points": [], "polygons": [],
            "ghost_polygons": [], "center": None, "infer_ms": None,
            "model_fps": None, "frame_id": None, "ts": 0.0, "camera_running": True,
        }, fail={"select": {
            "ok": False, "code": "SEGMENT_LOADING", "message": "still loading",
        }})
        with with_service(stub):
            with self.assertRaises(HTTPException) as cm:
                segment_click(ClickRequest(x=0.5, y=0.5))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "SEGMENT_LOADING")

    def test_clear_ok(self):
        stub = StubService()
        with with_service(stub):
            res = segment_clear()
        self.assertTrue(res["ok"])
        self.assertIn("clear", stub.calls)

    def test_stop_ok(self):
        stub = StubService()
        with with_service(stub):
            res = segment_stop()
        self.assertTrue(res["ok"])
        self.assertIn("stop", stub.calls)

    def test_invalid_coordinates_maps_to_422(self):
        stub = StubService(fail={"select": {
            "ok": False, "code": "SEGMENT_INVALID_COORDINATES", "message": "bad",
        }})
        status_payload = {
            "ok": True, "enabled": True, "state": "idle", "has_target": False,
            "loading": False, "error": None, "points": [], "polygons": [],
            "ghost_polygons": [], "center": None, "infer_ms": None,
            "model_fps": None, "frame_id": None, "ts": 0.0, "camera_running": True,
        }
        stub._status = status_payload
        with with_service(stub):
            with self.assertRaises(HTTPException) as cm:
                segment_click(ClickRequest(x=0.5, y=0.5))
        self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual(cm.exception.detail["code"], "SEGMENT_INVALID_COORDINATES")


if __name__ == "__main__":
    unittest.main()