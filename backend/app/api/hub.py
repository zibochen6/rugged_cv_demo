"""Visual Hub control/status/event/stream API."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..hub.occupancy import OccupancyError
from ..hub.runtime import get_hub_runtime
from ..recording.runtime import RecordingConflict

router = APIRouter(prefix="/api/hub", tags=["hub"])

KNOWN_MODULES = {"front", "rear", "dms"}


class ModuleConfigBody(BaseModel):
    fatigue_enabled: Optional[bool] = None
    helmet_enabled: Optional[bool] = None
    fatigue_alarm_buzzer: Optional[bool] = None
    danger_m: Optional[float] = Field(default=None, ge=0.3, le=5.0)
    warning_m: Optional[float] = Field(default=None, ge=0.5, le=10.0)
    buzzer: Optional[bool] = None
    recording: Optional[bool] = None


def _module_or_404(module_id: str):
    if module_id not in KNOWN_MODULES:
        raise HTTPException(status_code=404, detail={
            "code": "UNKNOWN_MODULE",
            "message": f"unknown module {module_id}",
        })
    return get_hub_runtime()


def _raise_occupancy(exc: OccupancyError) -> None:
    raise HTTPException(status_code=409, detail={
        "code": exc.code,
        "message": exc.message,
        "recoverable": True,
    })


def _raise_mode_conflict(exc: RecordingConflict) -> None:
    raise HTTPException(status_code=409, detail={
        "code": exc.code,
        "message": str(exc),
        "recoverable": True,
    })


@router.get("/status")
def hub_status():
    return get_hub_runtime().snapshot()


@router.get("/events")
def hub_events(limit: int = 80, since_id: int = 0):
    runtime = get_hub_runtime()
    return {"ok": True, "events": runtime.events.recent(limit=limit, since_id=since_id)}


@router.get("/modules/{module_id}")
def module_status(module_id: str):
    runtime = _module_or_404(module_id)
    try:
        return runtime.module_status(module_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"code": "UNKNOWN_MODULE"})


@router.post("/modules/{module_id}/start")
def start_module(module_id: str):
    runtime = _module_or_404(module_id)
    try:
        return {"ok": True, "module": runtime.start_module(module_id)}
    except RecordingConflict as exc:
        _raise_mode_conflict(exc)
    except OccupancyError as exc:
        _raise_occupancy(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={
            "code": "MODULE_START_FAILED",
            "message": str(exc),
        })


@router.post("/modules/{module_id}/stop")
def stop_module(module_id: str):
    runtime = _module_or_404(module_id)
    return {"ok": True, "module": runtime.stop_module(module_id)}


@router.post("/modules/{module_id}/restart")
def restart_module(module_id: str):
    runtime = _module_or_404(module_id)
    try:
        return {"ok": True, "module": runtime.restart_module(module_id)}
    except RecordingConflict as exc:
        _raise_mode_conflict(exc)
    except OccupancyError as exc:
        _raise_occupancy(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={
            "code": "MODULE_RESTART_FAILED",
            "message": str(exc),
        })


@router.post("/actions/start-all")
def start_all():
    try:
        return get_hub_runtime().start_all()
    except RecordingConflict as exc:
        _raise_mode_conflict(exc)


@router.post("/actions/stop-all")
def stop_all():
    return get_hub_runtime().stop_all()


@router.post("/modules/{module_id}/config")
def config_module(module_id: str, body: ModuleConfigBody):
    runtime = _module_or_404(module_id)
    raw = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    payload: Dict[str, Any] = {k: v for k, v in raw.items() if v is not None}
    if not payload:
        raise HTTPException(status_code=400, detail={
            "code": "EMPTY_CONFIG",
            "message": "no supported config keys provided",
        })
    if module_id == "rear":
        current = runtime.module_status("rear").get("metrics") or {}
        danger = float(payload.get("danger_m", current.get("danger_m", 1.5)))
        warning = float(payload.get("warning_m", current.get("warning_m", 3.0)))
        if not (0.3 <= danger <= 5.0 and 0.5 <= warning <= 10.0 and danger < warning):
            raise HTTPException(status_code=422, detail={
                "code": "INVALID_REAR_THRESHOLDS",
                "message": "危险距离须为 0.3–5.0m、警告距离须为 0.5–10.0m，且危险距离必须小于警告距离",
            })
        payload["danger_m"] = danger
        payload["warning_m"] = warning
    try:
        result = runtime.apply_module_config(module_id, payload)
    except KeyError:
        raise HTTPException(status_code=400, detail={
            "code": "CONFIG_UNSUPPORTED",
            "message": f"{module_id} does not accept runtime config",
        })
    except Exception as exc:
        raise HTTPException(status_code=502, detail={
            "code": "CONFIG_PROXY_FAILED",
            "message": str(exc),
        })
    return {"ok": True, "config": result}


@router.get("/stream/{module_id}")
async def stream_module(module_id: str, request: Request):
    runtime = _module_or_404(module_id)
    if module_id == "front":
        from .camera import stream_mjpeg
        return await stream_mjpeg()
    status = runtime.module_status(module_id)
    native = status.get("native_url")
    if not native:
        spec = runtime.config.module_specs()[module_id]
        native = f"http://127.0.0.1:{spec.port}{spec.stream_path}"
    timeout = httpx.Timeout(connect=2.0, read=None, write=5.0, pool=5.0)

    async def pump():
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", native) as resp:
                    if resp.status_code >= 400:
                        yield b"--frame\r\nContent-Type: text/plain\r\n\r\nmodule stream unavailable\r\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        if await request.is_disconnected():
                            break
                        yield chunk
        except Exception:
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\nmodule stream unavailable\r\n"

    return StreamingResponse(
        pump(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
