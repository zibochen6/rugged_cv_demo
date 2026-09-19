"""Camera device discovery."""
import os
import cv2
from typing import List, Optional
from .models import CameraInfo


def enumerate_devices() -> List[CameraInfo]:
    """Enumerate all available V4L2 video devices.
    
    Returns:
        List of CameraInfo for each discovered camera.
    """
    devices = []
    
    # Check /dev/video*
    for i in range(10):
        device_path = f"/dev/video{i}"
        if not os.path.exists(device_path):
            continue
        
        info = CameraInfo(device=device_path, index=i)
        
        # Try to open and query capabilities
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if cap.isOpened():
            try:
                info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                info.fps = cap.get(cv2.CAP_PROP_FPS)
                
                fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
                if fourcc_int > 0:
                    info.fourcc = (
                        chr(fourcc_int & 0xFF) +
                        chr((fourcc_int >> 8) & 0xFF) +
                        chr((fourcc_int >> 16) & 0xFF) +
                        chr((fourcc_int >> 24) & 0xFF)
                    )
                
                # Try to read a frame to verify
                ret, frame = cap.read()
                if not ret:
                    cap.release()
                    continue
            except Exception:
                pass
            finally:
                cap.release()
        else:
            cap.release()
            continue
        
        devices.append(info)
    
    return devices


def get_default_device() -> Optional[str]:
    """Get the first available camera device.
    
    Returns:
        Device path like "/dev/video0" or None if no camera found.
    """
    devices = enumerate_devices()
    if devices:
        return devices[0].device
    return None
