"""System info and health API."""
from fastapi import APIRouter
import cv2
import sys
import platform

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "visual-hub",
        "python": sys.version.split()[0],
        "opencv": cv2.__version__,
        "platform": platform.system(),
    }


@router.get("/system/info")
async def system_info():
    """Get system and environment information."""
    return {
        "python_version": sys.version.split()[0],
        "opencv_version": cv2.__version__,
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "build_info": cv2.getBuildInformation()[:500] if hasattr(cv2, 'getBuildInformation') else "N/A",
    }


@router.get("/system/openapi")
async def openapi_info():
    """Get OpenCV capabilities."""
    return {
        "has_v4l2": hasattr(cv2, 'CAP_V4L2'),
        "has_gstreamer": hasattr(cv2, 'CAP_GSTREAMER'),
        "has_msmf": hasattr(cv2, 'CAP_MSMF'),
        "supported_backends": {
            "V4L2": hasattr(cv2, 'CAP_V4L2'),
            "GStreamer": hasattr(cv2, 'CAP_GSTREAMER'),
        }
    }
