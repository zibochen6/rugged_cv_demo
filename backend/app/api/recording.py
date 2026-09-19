"""Pure camera recording API; it never starts an inference module."""
from __future__ import annotations

import re
from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..hub.runtime import get_hub_runtime
from ..recording.runtime import (
    CAMERAS,
    RecordingConflict,
    StorageError,
    get_recording_runtime,
)


router = APIRouter(prefix="/api/recording", tags=["recording"])


def _sources() -> dict[str, str]:
    specs = get_hub_runtime().config.module_specs()
    return {camera: specs[camera].camera for camera in CAMERAS}


def _camera_or_404(camera: str) -> None:
    if camera not in CAMERAS:
        raise HTTPException(status_code=404, detail={
            "code": "UNKNOWN_RECORDING_CAMERA",
            "message": f"unknown recording camera {camera}",
        })


def _raise_recording_error(exc: Exception) -> None:
    if isinstance(exc, RecordingConflict):
        status = 409
    elif isinstance(exc, StorageError):
        status = 507
    else:
        status = 500
    raise HTTPException(status_code=status, detail={
        "code": getattr(exc, "code", "RECORDING_ERROR"),
        "message": str(exc),
    })


@router.get("/status")
def recording_status():
    return get_recording_runtime().snapshot()


@router.post("/mode/enter")
def enter_recording_mode():
    try:
        return get_recording_runtime().enter(get_hub_runtime())
    except Exception as exc:  # noqa: BLE001
        _raise_recording_error(exc)


@router.post("/mode/exit")
def exit_recording_mode():
    try:
        return get_recording_runtime().exit()
    except Exception as exc:  # noqa: BLE001
        _raise_recording_error(exc)


@router.post("/cameras/{camera}/start")
def start_recording(camera: str):
    _camera_or_404(camera)
    try:
        return get_recording_runtime().start(camera, _sources())
    except Exception as exc:  # noqa: BLE001
        _raise_recording_error(exc)


@router.post("/cameras/{camera}/stop")
def stop_recording(camera: str):
    _camera_or_404(camera)
    return get_recording_runtime().stop(camera)


@router.post("/actions/start-all")
def start_all_recordings():
    try:
        return get_recording_runtime().start_all(_sources())
    except Exception as exc:  # noqa: BLE001
        _raise_recording_error(exc)


@router.post("/actions/stop-all")
def stop_all_recordings():
    return get_recording_runtime().stop_all()


@router.get("/stream/{camera}")
def recording_stream(camera: str):
    _camera_or_404(camera)
    return StreamingResponse(
        get_recording_runtime().preview(camera),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/files")
def list_recordings(camera: Optional[str] = None):
    if camera is not None:
        _camera_or_404(camera)
    return {"ok": True, "files": get_recording_runtime().files(camera)}


def _file_or_404(recording_id: str):
    try:
        return get_recording_runtime().file_path(recording_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={
            "code": "RECORDING_NOT_FOUND",
            "message": "recording does not exist or is not complete",
        })


def _video_response(path, request: Request, *, download: bool) -> Response:
    """Serve MP4 with explicit Range support for the installed Starlette."""
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    raw_range = request.headers.get("range")
    if raw_range:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw_range.strip())
        if match is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        if not first and not last:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        else:
            length = min(int(last), size)
            start, end = size - length, size - 1
        if start < 0 or start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        status = 206

    length = end - start + 1
    disposition = "attachment" if download else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'{disposition}; filename="{path.name}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def read_chunks() -> Iterator[bytes]:
        remaining = length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(read_chunks(), status_code=status, media_type="video/mp4", headers=headers)


@router.get("/files/{recording_id}/play")
def play_recording(recording_id: str, request: Request):
    return _video_response(_file_or_404(recording_id), request, download=False)


@router.get("/files/{recording_id}/download")
def download_recording(recording_id: str, request: Request):
    return _video_response(_file_or_404(recording_id), request, download=True)
