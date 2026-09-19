"""
Visual Hub - FastAPI backend.

Main application entry point with lifespan management for graceful shutdown.
Camera sources and ports come from the environment (see backend/app/hub/config.py);
this module only wires routers, the MJPEG encoder and the hub/recording runtimes.
"""
import logging
import sys
import os

# Ensure project root is on sys.path BEFORE any submodule imports, so absolute
# `from app.X.Y` resolves to seg_demo/app/ (the original application package),
# not backend/app/ (which only contains the API shim).
_BACKEND_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # seg_demo/backend
_ROOT_DIR    = os.path.dirname(_BACKEND_PKG)                                # seg_demo
for _p in (_BACKEND_PKG, _ROOT_DIR):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, _ROOT_DIR)
sys.path.append(_BACKEND_PKG)

import signal
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import argparse
import os

from .config import get_config, AppConfig
from .camera.manager import get_camera_manager
from .streaming.mjpeg import MjpegEncoder, set_mjpeg_encoder, get_mjpeg_encoder
from .hub.runtime import get_hub_runtime
from .recording.runtime import get_recording_runtime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown of:
    - CameraManager
    - MJPEG Encoder
    - Any background tasks
    """
    config = get_config()
    camera = get_camera_manager()
    
    logger.info("=" * 50)
    logger.info("Visual Hub starting...")
    logger.info("=" * 50)
    
    # Startup
    try:
        # Create and start MJPEG encoder
        encoder = MjpegEncoder(
            camera_manager=camera,
            preview_width=config.stream.preview_width,
            preview_height=config.stream.preview_height,
            jpeg_quality=config.stream.jpeg_quality,
            target_fps=config.stream.target_fps,
        )
        set_mjpeg_encoder(encoder)
        encoder.start()
        
        logger.info("Camera roles are idle until started from Visual Hub")
        logger.info(f"Stream: {config.stream.preview_width}x{config.stream.preview_height} @ Q{config.stream.jpeg_quality}")
        logger.info(f"Server: http://0.0.0.0:{config.server.port}")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise
    
    try:
        get_hub_runtime().start()
        logger.info("Visual Hub runtime started")
    except Exception as e:
        logger.error(f"Visual Hub startup failed: {e}")

    yield
    
    # Shutdown
    logger.info("Shutting down...")

    try:
        try:
            get_recording_runtime().shutdown()
        except Exception as e:
            logger.error(f"Recording shutdown failed: {e}")
        try:
            get_hub_runtime().stop()
        except Exception as e:
            logger.error(f"Visual Hub shutdown failed: {e}")

        # Stop MJPEG encoder
        encoder = get_mjpeg_encoder()
        if encoder is not None:
            encoder.stop()
            set_mjpeg_encoder(None)

        # Stop camera
        camera.stop()

        logger.info("Shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


def create_app(config: AppConfig = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = get_config()
    
    app = FastAPI(
        title="Visual Hub",
        description="Forklift visual hub: front segment, rear warning, cabin DMS",
        version="1.1.0",
        lifespan=lifespan,
    )
    
    # Import and include routers. The front module's MJPEG source lives in
    # .api.camera (mounted here so /api/camera/stream.mjpg stays the front
    # module's stream_path); the hub proxies it for the browser.
    from .api import system, camera, hub, recording
    from .segment.api import router as segment_router

    app.include_router(system.router)
    app.include_router(camera.router)
    app.include_router(segment_router)
    app.include_router(hub.router)
    app.include_router(recording.router)
    
    # Serve frontend (if built)
    frontend_dist = "frontend/dist"
    import os
    if os.path.exists(frontend_dist):
        # Mount at "/" so that absolute paths in index.html (/assets/, /favicon.ico)
        # resolve directly to frontend/dist/assets/ etc.
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")

        @app.get("/")
        async def root():
            return RedirectResponse(url="/index.html")
    else:
        @app.get("/")
        async def root():
            return {
                "service": "visual-hub",
                "version": "1.1.0",
                "docs": "/docs",
                "endpoints": {
                    "health": "/api/health",
                    "hub": "/api/hub/status",
                    "stream": "/api/hub/stream/front",
                }
            }
    
    return app


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Visual Hub (three-camera)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    args = parser.parse_args()

    config = get_config()

    # Override with CLI args
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    
    # Create app
    app = create_app(config)
    
    # Run server
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        access_log=True,
        timeout_keep_alive=30,  # Keep connections alive for low latency
        # Bound the graceful-shutdown wait: browsers keep MJPEG/SSE
        # long-poll connections open, and without this cap a single
        # Ctrl+C would wait for them forever ("Waiting for connections
        # to close"). After the timeout uvicorn cancels them and runs
        # the lifespan shutdown (pose worker -> encoder -> camera).
        timeout_graceful_shutdown=5,
        h11_max_incomplete_event_size=16384,
    )


if __name__ == "__main__":
    main()
