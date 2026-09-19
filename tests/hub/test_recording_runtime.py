"""Lifecycle tests for the inference-free recording runtime."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backend.app.hub.config import HubConfig
from backend.app.hub.runtime import HubRuntime, set_hub_runtime
from backend.app.recording.runtime import (
    RecordingConflict,
    RecordingRuntime,
    set_recording_runtime,
)


class FakeCapture:
    def __init__(self) -> None:
        self.released = False

    def isOpened(self) -> bool:
        return True

    def read(self):
        return True, np.zeros((720, 1280, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


class FakeWriter:
    def __init__(self, pipeline: str) -> None:
        match = re.search(r'filesink location="([^"]+)"', pipeline)
        assert match is not None
        self.path = Path(match.group(1))
        self.released = False

    def isOpened(self) -> bool:
        return True

    def write(self, _frame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"fake-mp4")

    def release(self) -> None:
        self.released = True


class TestRecordingRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = RecordingRuntime(root=self.tmp.name)

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self.tmp.cleanup()

    @staticmethod
    def _sources():
        return {"front": "rtsp://front", "rear": "rtsp://rear", "dms": "usb:0"}

    def test_enter_stops_inference_and_exit_requires_no_active_recordings(self):
        hub = mock.Mock()
        entered = self.runtime.enter(hub)
        self.assertEqual(entered["mode"], "recording")
        hub.stop_all.assert_called_once_with()
        self.runtime._sessions["front"] = mock.Mock()
        with self.assertRaises(RecordingConflict):
            self.runtime.exit()
        self.runtime._sessions.clear()
        self.assertEqual(self.runtime.exit()["mode"], "inference")

    @mock.patch("backend.app.recording.runtime.cv2.VideoWriter")
    @mock.patch("backend.app.recording.runtime.open_source")
    def test_recording_finalizes_and_catalog_hides_path(self, open_source, writer_ctor):
        capture = FakeCapture()
        open_source.return_value = (capture, "front-source", 0.0)
        writer_ctor.side_effect = lambda pipeline, *_args: FakeWriter(pipeline)
        self.runtime.enter(mock.Mock())
        self.runtime.start("front", self._sources())
        time.sleep(0.08)
        self.runtime.stop("front")

        files = self.runtime.files("front")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["state"], "complete")
        self.assertNotIn("path", files[0])
        path = self.runtime.file_path(files[0]["id"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".mp4")
        self.assertTrue(capture.released)

    def test_file_path_rejects_unmanaged_or_failed_files(self):
        with self.assertRaises(KeyError):
            self.runtime.file_path("../../etc/passwd")
        outside = Path(self.tmp.name).parent / "outside.mp4"
        outside.write_bytes(b"nope")
        self.runtime._files["outside"] = mock.Mock(state="complete", path=str(outside))
        with self.assertRaises(KeyError):
            self.runtime.file_path("outside")

    @mock.patch("backend.app.recording.runtime.cv2.VideoWriter")
    @mock.patch("backend.app.recording.runtime.open_source")
    def test_start_all_rolls_back_started_sessions_on_failure(self, open_source, writer_ctor):
        open_source.side_effect = [
            (FakeCapture(), "front", 0.0),
            (None, "rear", 0.0),
        ]
        writer_ctor.side_effect = lambda pipeline, *_args: FakeWriter(pipeline)
        self.runtime.enter(mock.Mock())
        with self.assertRaises(Exception):
            self.runtime.start_all(self._sources())
        self.assertEqual(self.runtime.snapshot()["cameras"]["front"]["state"], "idle")


class TestRecordingHubBoundary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.recording = RecordingRuntime(root=self.tmp.name)
        self.hub = HubRuntime(HubConfig(root=self.tmp.name, front_camera="rtsp://front", rear_camera="rtsp://rear"))
        set_recording_runtime(self.recording)
        set_hub_runtime(self.hub)

    def tearDown(self) -> None:
        self.hub.events.close()
        set_hub_runtime(None)
        set_recording_runtime(None)
        self.tmp.cleanup()

    def test_inference_start_conflicts_in_recording_mode(self):
        self.recording.enter(mock.Mock())
        with self.assertRaises(RecordingConflict):
            self.hub.start_all()
        self.assertEqual(self.hub.snapshot()["operation_mode"], "recording")

