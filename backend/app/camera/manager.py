"""
CameraManager - Single VideoCapture owner with graceful shutdown.

This is the CORE component of the entire system. All camera access MUST go
through this manager to ensure:
1. Only ONE VideoCapture exists at any time
2. Graceful shutdown releases ALL resources (Ctrl+C safe)
3. Multiple consumers (MJPEG stream, calibration) share the same capture

Architecture:
    CameraManager
        ├── VideoCapture (opened once)
        ├── capture_thread (reads frames continuously)
        ├── latest_frame (atomic: frame + frame_id + timestamp)
        └── consumers access via get_latest_frame()
"""
import threading
import time
import logging
import os
from typing import Optional, Tuple
from dataclasses import dataclass

import cv2
import numpy as np

from .models import CameraState, CameraInfo, FrameData
from .discovery import enumerate_devices

logger = logging.getLogger(__name__)


class CameraError(Exception):
    """Base camera error."""
    def __init__(self, message: str, code: str, recoverable: bool = True):
        super().__init__(message)
        self.message = message
        self.code = code
        self.recoverable = recoverable


class CameraNotFoundError(CameraError):
    """Camera device not found."""
    def __init__(self, device: str):
        super().__init__(
            f"Camera {device} not found",
            "CAMERA_NOT_FOUND",
            recoverable=True
        )


class CameraOpenError(CameraError):
    """Failed to open camera."""
    def __init__(self, device: str, reason: str):
        super().__init__(
            f"Cannot open camera {device}: {reason}",
            "CAMERA_OPEN_FAILED",
            recoverable=True
        )


class CameraBusyError(CameraError):
    """Camera is busy (already in use)."""
    def __init__(self, device: str):
        super().__init__(
            f"Camera {device} is already in use",
            "CAMERA_BUSY",
            recoverable=True
        )


@dataclass
class CameraConfig:
    """Camera configuration."""
    device: str = "/dev/video0"
    width: int = 1280
    height: int = 720
    fps: int = 30
    fourcc: str = "MJPG"  # Preferred format for USB UVC


class CameraManager:
    """
    Single camera owner with graceful shutdown.
    
    This class MUST be used as a singleton. All camera access goes through
    this manager to ensure proper resource management.
    
    Key features:
    - Single VideoCapture instance (no duplicates)
    - Capture thread with stop_event for clean shutdown
    - Atomic latest_frame access (frame_id + timestamp)
    - State machine for lifecycle management
    - Idempotent start/stop/restart operations
    
    Usage:
        manager = CameraManager()
        manager.start("/dev/video0")
        frame_id, frame = manager.get_latest_frame()
        manager.stop()  # Always releases resources
    """
    
    _instance: Optional['CameraManager'] = None
    _instance_lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        """Initialize the camera manager."""
        if self._initialized:
            return
        
        # VideoCapture instance (only one!)
        self._cap: Optional[cv2.VideoCapture] = None
        
        # Configuration
        self._config: Optional[CameraConfig] = None
        
        # State machine
        self._state: CameraState = CameraState.DISCONNECTED
        self._state_lock = threading.RLock()
        
        # Capture thread
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        
        # Latest frame (atomic access via lock)
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_frame_id: int = 0
        self._latest_timestamp: float = 0.0
        self._capture_fps: float = 0.0
        self._last_fps_sample_at: float = 0.0
        self._fps_sample_frames: int = 0
        self._frame_lock = threading.RLock()
        
        # Error tracking
        self._error_message: Optional[str] = None

        # RTSP stream supervision.
        #
        # OpenCV's GStreamer backend blocks inside read() for as long as the
        # RTSP source is silent, so a stalled stream (cable pulled, camera
        # power-cycled, PoE port down) can never be noticed from inside
        # _capture_loop: the frame simply stops advancing while the state stays
        # RUNNING and the UI shows a frozen "Connecting to camera...".
        # Observed 2026-09-19: frame_age kept growing past 230 s with
        # capture_fps still reporting the last good average.
        # The supervisor watches the frame timestamp and re-opens the stream,
        # mirroring the camera-gone reconnect the rear module already performs
        # in app/warn_app.py.
        self._shutdown = False
        self._supervisor_thread: Optional[threading.Thread] = None
        self._last_reconnect_at = 0.0
        self._stall_timeout_s = float(
            os.environ.get("SEG_DEMO_RTSP_STALL_TIMEOUT_S", "8"))
        self._reconnect_min_interval_s = float(
            os.environ.get("SEG_DEMO_RTSP_RECONNECT_MIN_INTERVAL_S", "2"))

        self._initialized = True
        logger.info("CameraManager initialized (singleton)")
    
    @property
    def state(self) -> CameraState:
        """Get current camera state."""
        with self._state_lock:
            return self._state
    
    @property
    def error_message(self) -> Optional[str]:
        """Get error message if in ERROR state."""
        with self._state_lock:
            return self._error_message
    
    @property
    def is_running(self) -> bool:
        """Check if camera is running."""
        return self.state == CameraState.RUNNING
    
    def _set_state(self, new_state: CameraState, error_msg: Optional[str] = None) -> None:
        """Update state with locking."""
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            self._error_message = error_msg
            if old_state != new_state:
                logger.info(f"Camera state: {old_state.value} -> {new_state.value}")
    
    def _capture_loop(self) -> None:
        """
        Capture loop - runs in dedicated thread.
        
        This is the ONLY place where VideoCapture.read() is called.
        All consumers get frames via get_latest_frame().
        """
        logger.info("Capture thread started")
        consecutive_failures = 0
        max_failures = 10
        
        while not self._stop_event.is_set():
            if self._cap is None or not self._cap.isOpened():
                logger.warning("VideoCapture not available")
                break
            
            try:
                ret, frame = self._cap.read()
                
                if not ret or frame is None:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        logger.error(f"Camera read failed {max_failures} times consecutively")
                        self._set_state(CameraState.ERROR, "Camera read failed")
                        break
                    time.sleep(0.01)
                    continue
                
                consecutive_failures = 0
                
                # Update latest frame atomically
                with self._frame_lock:
                    self._latest_frame = frame
                    self._latest_frame_id += 1
                    self._latest_timestamp = time.time()
                    self._fps_sample_frames += 1
                    elapsed = self._latest_timestamp - self._last_fps_sample_at
                    if elapsed >= 1.0:
                        instant = self._fps_sample_frames / elapsed
                        self._capture_fps = (
                            instant if self._capture_fps <= 0
                            else 0.8 * self._capture_fps + 0.2 * instant
                        )
                        self._last_fps_sample_at = self._latest_timestamp
                        self._fps_sample_frames = 0
                
                # Small sleep to prevent CPU spinning
                # (VideoCapture blocks by default, but this handles edge cases)
                time.sleep(0.001)
                
            except Exception as e:
                logger.error(f"Capture loop exception: {e}")
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    self._set_state(CameraState.ERROR, str(e))
                    break
        
        logger.info("Capture thread exiting")

    # ------------------------------------------------------------------
    # RTSP stream supervision
    # ------------------------------------------------------------------
    def _is_rtsp(self) -> bool:
        return bool(self._config and self._config.device.startswith("rtsp://"))

    def _ensure_supervisor(self) -> None:
        """Start the stream supervisor once (idempotent)."""
        if self._supervisor_thread is not None and self._supervisor_thread.is_alive():
            return
        self._supervisor_thread = threading.Thread(
            target=self._supervise, name="CameraStreamSupervisor", daemon=True)
        self._supervisor_thread.start()

    def _supervise(self) -> None:
        """Re-open the RTSP stream when it stalls or errors out.

        A deliberate stop() leaves the state DISCONNECTED, which this loop
        ignores, so an operator-requested stop is never undone.
        """
        backoff = self._reconnect_min_interval_s
        while not self._shutdown:
            time.sleep(1.0)
            if not self._is_rtsp():
                continue

            state = self.state
            if state == CameraState.RUNNING:
                with self._frame_lock:
                    last = self._latest_timestamp
                if last and (time.time() - last) <= self._stall_timeout_s:
                    backoff = self._reconnect_min_interval_s
                    continue
                why = (f"no frame for {time.time() - last:.0f}s"
                       if last else "no frame yet")
            elif state == CameraState.ERROR:
                why = "capture error"
            else:
                continue

            now = time.time()
            if now - self._last_reconnect_at < backoff:
                continue
            self._last_reconnect_at = now
            backoff = min(backoff * 2.0, 30.0)

            logger.warning("RTSP stream unhealthy (%s); reconnecting", why)
            try:
                device = self._config.device
                self.stop()
                self.start(device)
                logger.info("RTSP stream reconnected")
                backoff = self._reconnect_min_interval_s
            except Exception as exc:  # noqa: BLE001 - supervision must not die
                logger.error("RTSP reconnect failed: %s", exc)

    def start(self, device: str = "/dev/video0", width: int = 1280, 
              height: int = 720, fps: int = 30) -> CameraState:
        """
        Start camera capture.
        
        Args:
            device: V4L2 device path (e.g., "/dev/video0")
            width: Requested frame width
            height: Requested frame height
            fps: Requested frames per second
        
        Returns:
            New camera state
        
        Raises:
            CameraNotFoundError: Device doesn't exist
            CameraOpenError: Cannot open device
            CameraBusyError: Device already in use
        """
        # Validate state
        with self._state_lock:
            if self._state == CameraState.RUNNING:
                logger.info("Camera already running, no-op")
                return self._state
            if self._state == CameraState.OPENING:
                logger.info("Camera already opening, no-op")
                return self._state
        
        self._set_state(CameraState.OPENING)
        
        try:
            # Stop existing capture if any
            self._stop_internal()
            
            is_rtsp = device.startswith("rtsp://")
            if device.startswith("usb:"):
                device = "/dev/video" + device.split(":", 1)[1]

            # Open the fixed source. RTSP uses JetPack's NVIDIA decoder and
            # a one-buffer appsink so stale frames are dropped, never queued.
            if is_rtsp:
                from app.camera_source import rtsp_gst_pipeline
                codec_order = ("h265", "h264")
                for codec in codec_order:
                    pipeline = rtsp_gst_pipeline(device, codec=codec, latency=120)
                    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                    if cap.isOpened():
                        self._cap = cap
                        break
                    cap.release()
                if self._cap is None:
                    raise CameraOpenError("PoE front camera", "RTSP/GStreamer open failed")
            elif device.startswith("/dev/video"):
                try:
                    device_idx = int(device.replace("/dev/video", ""))
                except ValueError:
                    raise CameraNotFoundError(device)
                if not os.path.exists(device):
                    raise CameraNotFoundError(device)
                logger.info("Opening USB camera %s", device)
                self._cap = cv2.VideoCapture(device_idx, cv2.CAP_V4L2)
                if not self._cap.isOpened():
                    self._cap.release()
                    self._cap = None
                    raise CameraBusyError(device)
                self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self._cap.set(cv2.CAP_PROP_FPS, fps)
            else:
                raise CameraNotFoundError(device)
            
            # Verify settings
            actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
            
            logger.info(f"Camera opened: {actual_width}x{actual_height} @ {actual_fps} fps")
            
            # Store config
            self._config = CameraConfig(
                device=device,
                width=actual_width,
                height=actual_height,
                fps=int(actual_fps) if actual_fps > 0 else fps
            )
            
            # Start capture thread
            self._stop_event.clear()
            self._last_fps_sample_at = time.time()
            self._fps_sample_frames = 0
            self._capture_fps = 0.0
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="CameraCaptureThread",
                daemon=True
            )
            self._capture_thread.start()
            
            # Wait briefly for first frame
            timeout = 3.0  # seconds
            start = time.time()
            while time.time() - start < timeout:
                with self._frame_lock:
                    if self._latest_frame is not None:
                        break
                time.sleep(0.01)
            
            with self._frame_lock:
                has_frame = self._latest_frame is not None
            if not has_frame:
                raise CameraOpenError(
                    "PoE front camera" if is_rtsp else device,
                    "no first frame within 3 seconds",
                )
            self._set_state(CameraState.RUNNING)
            self._ensure_supervisor()
            logger.info("Camera started successfully")
            return self._state
            
        except CameraError as e:
            # Leave a *recoverable* state. Staying in OPENING made every later
            # start() a no-op ("Camera already opening"), so a PoE camera that
            # was merely not reachable yet could never be opened again — the
            # module then reported "running" with CAPTURE 0.0 FPS forever.
            self._stop_internal()
            self._set_state(CameraState.ERROR, str(e))
            raise
        except Exception as e:
            self._stop_internal()
            error = CameraOpenError(device, str(e))
            self._set_state(CameraState.ERROR, str(e))
            raise error
    
    def _stop_internal(self) -> None:
        """Internal stop - releases resources without changing state."""
        logger.info("Stopping camera capture (internal)")
        
        # Signal stop
        self._stop_event.set()
        
        # Release first: a blocked GStreamer/V4L2 read must be unblocked
        # before waiting for the capture worker.
        if self._cap is not None:
            try:
                self._cap.release()
                logger.info("VideoCapture released")
            except Exception as e:
                logger.error(f"Error releasing VideoCapture: {e}")
            finally:
                self._cap = None

        # Wait for capture thread
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=3.0)
            if self._capture_thread.is_alive():
                logger.warning("Capture thread did not stop within timeout, forcing")
                self._capture_thread.join(timeout=0)  # Non-blocking
            self._capture_thread = None
        
        # Clear frame
        with self._frame_lock:
            self._latest_frame = None
            self._latest_timestamp = 0.0
            self._capture_fps = 0.0
    
    def stop(self) -> CameraState:
        """
        Stop camera capture and release resources.
        
        This method is SAFE to call multiple times and from any state.
        It always transitions to DISCONNECTED state.
        
        Returns:
            New camera state (DISCONNECTED)
        """
        logger.info("Stop requested")
        
        with self._state_lock:
            if self._state == CameraState.DISCONNECTED:
                return self._state
            
            if self._state == CameraState.STOPPING:
                logger.info("Already stopping, waiting")
                # Wait briefly then return
                time.sleep(0.1)
                return self._state
            
            self._set_state(CameraState.STOPPING)
        
        # Perform actual stop
        self._stop_internal()
        
        self._set_state(CameraState.DISCONNECTED)
        logger.info("Camera stopped, resources released")
        
        return self._state
    
    def restart(self, device: Optional[str] = None, width: int = 1280,
                height: int = 720, fps: int = 30) -> CameraState:
        """
        Restart camera with optional new settings.
        
        This is equivalent to stop() followed by start().
        
        Returns:
            New camera state
        """
        if device is None and self._config:
            device = self._config.device
        else:
            device = device or "/dev/video0"
        
        logger.info(f"Restart requested with device={device}")
        self.stop()
        return self.start(device, width, height, fps)
    
    def get_latest_frame(self) -> Tuple[int, Optional[np.ndarray]]:
        """
        Get the latest captured frame.
        
        Returns:
            Tuple of (frame_id, frame). frame_id increments each frame.
            frame is None if no frame available yet.
        
        Note:
            This is a non-blocking, zero-copy operation.
            The returned frame is a VIEW into the internal buffer.
            Do NOT modify the returned array!
        """
        with self._frame_lock:
            return self._latest_frame_id, self._latest_frame
    
    def get_full_res_frame(self) -> Tuple[int, Optional[np.ndarray]]:
        """
        Get the latest frame at FULL resolution for capture.
        
        This is used when user clicks "Capture Frame" to ensure
        we get the full resolution frame.
        
        Returns:
            Tuple of (frame_id, frame)
        """
        return self.get_latest_frame()
    
    def get_config(self) -> Optional[CameraConfig]:
        """Get current camera configuration."""
        return self._config

    def metrics(self) -> dict:
        """Public, credential-free capture health metrics."""
        with self._frame_lock:
            age = time.time() - self._latest_timestamp if self._latest_timestamp else None
            return {
                "capture_fps": round(self._capture_fps, 2),
                "frame_age_s": round(age, 3) if age is not None else None,
                "frame_id": self._latest_frame_id,
                "has_frame": self._latest_frame is not None,
            }
    
    def release(self) -> None:
        """
        Release all resources (alias for stop()).
        
        This is the preferred method for cleanup.
        """
        self._shutdown = True
        self.stop()
    
    def __del__(self):
        """Destructor - last resort cleanup."""
        if hasattr(self, '_cap') and self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass


def get_camera_manager() -> CameraManager:
    """Get the singleton CameraManager instance."""
    return CameraManager()
