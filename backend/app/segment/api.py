"""Segment API endpoints (click-to-segment)."""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .service import get_segment_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/segment", tags=["segment"])


class ClickRequest(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0, description="normalized canvas x")
    y: float = Field(..., ge=0.0, le=1.0, description="normalized canvas y")
    label: int = Field(1, ge=0, le=1, description="1=foreground, 0=background")


def _raise_for_result(result: dict) -> None:
    """Translate a service result into an HTTP error when not ok."""
    if result.get("ok"):
        return
    code = str(result.get("code") or "SEGMENT_ERROR")
    status = 422 if code == "SEGMENT_INVALID_COORDINATES" else 409
    raise HTTPException(
        status_code=status,
        detail={
            "code": code,
            "message": result.get("message") or "segment request failed",
            "recoverable": True,
        },
    )


def _ensure_inference_allowed() -> None:
    # Segment endpoints are public for the front-view interaction. They must
    # obey the same mode boundary as the hub controls.
    from ..recording.runtime import get_recording_runtime
    if get_recording_runtime().mode == "recording":
        raise HTTPException(status_code=409, detail={
            "code": "RECORDING_ACTIVE",
            "message": "exit recording mode before starting front segmentation",
            "recoverable": True,
        })


@router.get("/status")
def segment_status():
    return get_segment_service().status()


@router.post("/start")
def segment_start():
    _ensure_inference_allowed()
    result = get_segment_service().start()
    _raise_for_result(result)
    return result


@router.post("/stop")
def segment_stop():
    return get_segment_service().stop()


@router.post("/click")
def segment_click(req: ClickRequest):
    _ensure_inference_allowed()
    svc = get_segment_service()
    if svc.status().get("has_target"):
        result = svc.add_point(req.x, req.y, req.label)
    else:
        result = svc.select_target(req.x, req.y, req.label)
    _raise_for_result(result)
    return result


@router.post("/clear")
def segment_clear():
    result = get_segment_service().clear_target()
    _raise_for_result(result)
    return result
