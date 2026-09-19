"""Three-camera, inference-free recording runtime.

Each session owns one capture handle and one hardware H.264 writer.  Frames are
also retained as a small JPEG preview; no model or inference service is loaded.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import cv2
import numpy as np

from app.camera_source import open_source


CAMERAS = ("front", "rear", "dms")
TARGET_SIZE = (1920, 1080)
TARGET_FPS = 15.0
MAX_DURATION_S = 30 * 60
MIN_FREE_BYTES = 2 * 1024 ** 3
BITRATE_BPS = 5_000_000


class RecordingError(RuntimeError):
    code = "RECORDING_ERROR"


class RecordingConflict(RecordingError):
    code = "RECORDING_ACTIVE"


class StorageError(RecordingError):
    code = "STORAGE_UNAVAILABLE"


@dataclass
class RecordingFile:
    id: str
    camera: str
    path: str
    started_at: float
    ended_at: Optional[float] = None
    duration_s: float = 0.0
    size_bytes: int = 0
    reason: Optional[str] = None
    state: str = "recording"

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("path", None)
        return data


@dataclass
class CaptureSession:
    camera: str
    source: str
    root: Path
    file: RecordingFile
    capture: Any = None
    writer: Any = None
    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.time)
    latest_jpeg: Optional[bytes] = None
    latest_jpeg_at: float = 0.0
    frames_written: int = 0
    last_write_at: float = 0.0
    error: Optional[str] = None


def _gstreamer_writer_pipeline(path: Path, width: int, height: int, fps: float) -> str:
    # JetPack 5 plugin names and properties.  No software fallback is used.
    safe = str(path).replace('"', "")
    return (
        "appsrc ! videoconvert ! video/x-raw,format=I420 "
        "! nvvidconv ! video/x-raw(memory:NVMM),format=I420 "
        f"! nvv4l2h264enc bitrate={BITRATE_BPS} iframeinterval={int(fps)} insert-sps-pps=true "
        "! h264parse ! qtmux ! "
        f"filesink location=\"{safe}\" sync=false"
    )


class RecordingRuntime:
    def __init__(self, root: Optional[str] = None, *, clock=time.time) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.root = Path(root or os.environ.get(
            "VISUAL_HUB_RECORDING_ROOT", str(Path.home() / "Videos" / "visual-hub")))
        self._project_root = project_root
        self._clock = clock
        self._lock = threading.RLock()
        self._mode = "inference"
        self._sessions: Dict[str, CaptureSession] = {}
        self._files: Dict[str, RecordingFile] = {}
        self._load_catalog()

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def enter(self, hub: Any) -> Dict[str, Any]:
        with self._lock:
            if self._mode == "recording":
                return self.snapshot()
            # Hold the mode lock through the stop transition. A concurrent
            # inference start blocks in HubRuntime until this becomes recording.
            hub.stop_all()
            self._mode = "recording"
        return self.snapshot()

    def exit(self) -> Dict[str, Any]:
        with self._lock:
            if self._sessions:
                raise RecordingConflict("stop all active recordings before leaving recording mode")
            self._mode = "inference"
        return self.snapshot()

    def start(self, camera: str, sources: Dict[str, str]) -> Dict[str, Any]:
        self._validate_camera(camera)
        with self._lock:
            if self._mode != "recording":
                raise RecordingConflict("enter recording mode before starting a recording")
            if camera in self._sessions:
                return self.snapshot()
        self._ensure_storage(1)
        source = sources[camera]
        session = self._open_session(camera, source)
        with self._lock:
            self._sessions[camera] = session
        session.thread = threading.Thread(
            target=self._capture_loop, args=(session,), name=f"record-{camera}", daemon=True)
        session.thread.start()
        return self.snapshot()

    def start_all(self, sources: Dict[str, str]) -> Dict[str, Any]:
        with self._lock:
            existing = set(self._sessions)
        missing = [camera for camera in CAMERAS if camera not in existing]
        self._ensure_storage(len(missing))
        started = []
        try:
            for camera in missing:
                self.start(camera, sources)
                started.append(camera)
        except Exception:
            for camera in reversed(started):
                self.stop(camera, reason="start-all-rollback")
            raise
        return self.snapshot()

    def stop(self, camera: str, reason: str = "manual") -> Dict[str, Any]:
        self._validate_camera(camera)
        with self._lock:
            session = self._sessions.pop(camera, None)
        if session is None:
            return self.snapshot()
        session.stop_event.set()
        if session.thread and session.thread is not threading.current_thread():
            session.thread.join(timeout=8)
        self._finalize(session, reason)
        return self.snapshot()

    def stop_all(self, reason: str = "manual") -> Dict[str, Any]:
        with self._lock:
            cameras = list(self._sessions)
        for camera in cameras:
            self.stop(camera, reason)
        return self.snapshot()

    def shutdown(self) -> None:
        self.stop_all("service-stop")

    def snapshot(self) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            cameras: Dict[str, Dict[str, Any]] = {}
            for camera in CAMERAS:
                session = self._sessions.get(camera)
                if session is None:
                    cameras[camera] = {"camera": camera, "state": "idle", "recording": False}
                    continue
                cameras[camera] = {
                    "camera": camera,
                    "state": "error" if session.error else "recording",
                    "recording": not bool(session.error),
                    "recording_id": session.file.id,
                    "elapsed_s": round(max(0.0, now - session.started_at), 1),
                    "size_bytes": self._path_size(Path(session.file.path)),
                    "frames_written": session.frames_written,
                    "error": session.error,
                }
            usage = shutil.disk_usage(self.root if self.root.exists() else self.root.parent)
            return {
                "ok": True,
                "mode": self._mode,
                "max_duration_s": MAX_DURATION_S,
                "storage": {"root": str(self.root), "free_bytes": usage.free, "total_bytes": usage.total},
                "cameras": cameras,
            }

    def preview(self, camera: str) -> Iterable[bytes]:
        self._validate_camera(camera)
        boundary = b"--frame\r\n"
        while True:
            with self._lock:
                session = self._sessions.get(camera)
                jpeg = session.latest_jpeg if session else None
            if session is None:
                return
            if jpeg:
                yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(0.1)

    def files(self, camera: Optional[str] = None) -> list[Dict[str, Any]]:
        if camera is not None:
            self._validate_camera(camera)
        with self._lock:
            values = [file for file in self._files.values() if camera is None or file.camera == camera]
        return [file.public() for file in sorted(values, key=lambda item: item.started_at, reverse=True)]

    def file_path(self, recording_id: str) -> Path:
        with self._lock:
            record = self._files.get(recording_id)
        if record is None or record.state != "complete":
            raise KeyError(recording_id)
        path = Path(record.path).resolve()
        root = self.root.resolve()
        if root not in path.parents or not path.is_file():
            raise KeyError(recording_id)
        return path

    def _open_session(self, camera: str, source: str) -> CaptureSession:
        self.root.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y-%m-%d")
        folder = self.root / camera / day
        folder.mkdir(parents=True, exist_ok=True)
        recording_id = f"{camera}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        temporary = folder / f"{recording_id}.part.mp4"
        capture, label, _ = open_source(source)
        if capture is None or not capture.isOpened():
            raise RecordingError(f"cannot open {camera} camera for recording")
        writer = cv2.VideoWriter(
            _gstreamer_writer_pipeline(temporary, *TARGET_SIZE, TARGET_FPS),
            cv2.CAP_GSTREAMER,
            0,
            TARGET_FPS,
            TARGET_SIZE,
            True,
        )
        if not writer.isOpened():
            capture.release()
            raise RecordingError("Jetson H.264 encoder is unavailable")
        file = RecordingFile(recording_id, camera, str(temporary), self._clock())
        return CaptureSession(camera=camera, source=label, root=folder, file=file, capture=capture, writer=writer)

    def _capture_loop(self, session: CaptureSession) -> None:
        next_write = self._clock()
        try:
            while not session.stop_event.is_set():
                ok, frame = session.capture.read()
                if not ok or frame is None:
                    raise RecordingError("camera stream ended")
                now = self._clock()
                if now >= next_write:
                    frame = cv2.resize(frame, TARGET_SIZE, interpolation=cv2.INTER_AREA)
                    session.writer.write(frame)
                    session.frames_written += 1
                    session.last_write_at = now
                    next_write = max(next_write + 1.0 / TARGET_FPS, now)
                if now - session.latest_jpeg_at >= 0.1:
                    preview = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
                    ok, buffer = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok:
                        session.latest_jpeg = buffer.tobytes()
                        session.latest_jpeg_at = now
                if now - session.started_at >= MAX_DURATION_S:
                    session.stop_event.set()
                    session.file.reason = "duration-limit"
                if session.frames_written and session.frames_written % 30 == 0:
                    self._ensure_storage(0)
        except Exception as exc:  # workers must report rather than crash the hub
            session.error = str(exc)
            session.stop_event.set()
        finally:
            if session.stop_event.is_set():
                with self._lock:
                    self._sessions.pop(session.camera, None)
                self._finalize(session, session.file.reason or ("error" if session.error else "manual"))

    def _finalize(self, session: CaptureSession, reason: str) -> None:
        # The worker and an API stop request can arrive together. One caller
        # owns finalization; the other sees a terminal/intermediate state.
        with self._lock:
            if session.file.state != "recording":
                return
            session.file.state = "finalizing"
        for handle in (session.writer, session.capture):
            try:
                handle.release()
            except Exception:
                pass
        old = Path(session.file.path)
        final = old.with_name(old.name.replace(".part.mp4", ".mp4"))
        session.file.ended_at = self._clock()
        session.file.duration_s = round(max(0.0, session.file.ended_at - session.file.started_at), 2)
        session.file.reason = reason
        if old.exists() and old.stat().st_size > 0 and not session.error:
            old.replace(final)
            session.file.path = str(final)
            session.file.state = "complete"
            session.file.size_bytes = final.stat().st_size
        else:
            session.file.state = "failed"
            session.file.size_bytes = self._path_size(old)
        with self._lock:
            self._files[session.file.id] = session.file
        self._write_metadata(session.file)

    def _ensure_storage(self, additional_streams: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        estimated = int((BITRATE_BPS / 8) * MAX_DURATION_S * max(0, additional_streams))
        if free < MIN_FREE_BYTES + estimated:
            raise StorageError("insufficient free space for recording")

    def _load_catalog(self) -> None:
        if not self.root.exists():
            return
        for metadata in self.root.rglob("*.json"):
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
                file = RecordingFile(**data)
                self._files[file.id] = file
            except Exception:
                continue
        # A service crash can leave a temporary MP4 with no metadata. Keep it
        # visible as failed evidence, never as a playable completed recording.
        for partial in self.root.rglob("*.part.mp4"):
            recording_id = partial.name[:-len(".part.mp4")]
            if recording_id in self._files:
                continue
            camera = next((part for part in partial.parts if part in CAMERAS), "unknown")
            modified = partial.stat().st_mtime
            self._files[recording_id] = RecordingFile(
                id=recording_id,
                camera=camera,
                path=str(partial),
                started_at=modified,
                ended_at=modified,
                size_bytes=self._path_size(partial),
                reason="interrupted-service-restart",
                state="failed",
            )

    def _write_metadata(self, file: RecordingFile) -> None:
        path = Path(file.path)
        metadata = path.with_suffix(".json")
        temporary = metadata.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(file), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(metadata)

    @staticmethod
    def _path_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _validate_camera(camera: str) -> None:
        if camera not in CAMERAS:
            raise KeyError(camera)


_RUNTIME: Optional[RecordingRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_recording_runtime() -> RecordingRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = RecordingRuntime()
        return _RUNTIME


def set_recording_runtime(runtime: Optional[RecordingRuntime]) -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        _RUNTIME = runtime
