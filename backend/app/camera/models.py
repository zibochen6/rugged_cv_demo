"""Camera state machine and models."""
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np


class CameraState(str, Enum):
    """Camera lifecycle states."""
    DISCONNECTED = "disconnected"
    OPENING = "opening"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class CameraInfo:
    """Camera device information."""
    device: str
    index: int
    name: Optional[str] = None
    width: int = 0
    height: int = 0
    fps: float = 0.0
    fourcc: Optional[str] = None


@dataclass
class FrameData:
    """Frame data with metadata."""
    frame: np.ndarray
    frame_id: int
    timestamp: float
    width: int
    height: int
