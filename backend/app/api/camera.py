"""Front-camera MJPEG stream endpoint.

Only `/api/camera/stream.mjpg` remains: it is the front module's `stream_path`
(see backend/app/hub/config.py) and backend/app/api/hub.py calls `stream_mjpeg()`
directly to serve `GET /api/hub/stream/front` from the same process. Camera
start/stop is owned by the hub (front runs in-process, rear/DMS are children),
so the old devices/status/start/stop/restart routes were removed.
"""
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..camera.manager import get_camera_manager
from ..streaming.mjpeg import get_mjpeg_encoder

router = APIRouter(prefix="/api/camera", tags=["camera"])


@router.get("/stream.mjpg")
async def stream_mjpeg():
    """
    MJPEG streaming endpoint.

    Returns multipart/x-mixed-replace stream with JPEG frames.
    Optimized for low latency (< 100ms end-to-end).
    """
    camera = get_camera_manager()
    encoder = get_mjpeg_encoder()

    # Check if camera is running
    if not camera.is_running:
        raise HTTPException(
            status_code=503,
            detail={"code": "CAMERA_NOT_RUNNING", "message": "Camera is not running"}
        )

    # Check if encoder exists
    if encoder is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "ENCODER_NOT_STARTED", "message": "Stream encoder not started"}
        )

    # Add client
    client = encoder.add_client()
    client_id = client.client_id

    async def generator():
        try:
            boundary = b"--frame"
            while True:
                client = encoder.get_client(client_id)
                if client is None or not client.active:
                    break

                # Send latest frame if client hasn't received it
                if client.latest_jpg_id != client.last_sent_id and client.latest_jpg is not None:
                    jpg = client.latest_jpg
                    client.last_sent_id = client.latest_jpg_id

                    yield (
                        boundary + b"\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpg)).encode() + b"\r\n"
                        b"\r\n" + jpg + b"\r\n"
                    )

                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            pass
        except GeneratorExit:
            pass
        finally:
            encoder.remove_client(client_id)

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )