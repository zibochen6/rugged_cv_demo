"""
Low-latency MJPEG streaming module.

Key design principles:
1. Single encoder thread processes all clients
2. Per-client ringbuffer (size=1) - latest frame only
3. Latest-frame drop policy - slow clients don't block others
4. Preview resolution downscaling (960x540) for faster encoding
5. Non-blocking frame access from CameraManager

Architecture:
    CameraManager
         │
         ▼ get_latest_frame()
    ┌─────────────────────────────────┐
    │         MjpegEncoder             │
    │  (single thread, all clients)    │
    └──────────┬──────────────────────┘
               │ broadcast (size=1 buffer)
         ┌─────┴─────┬─────────┐
         ▼           ▼         ▼
    Client 1    Client 2  Client 3
    (browser)    (browser)  (browser)
"""
import threading
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MjpegClient:
    """A single MJPEG client connection."""
    client_id: int
    latest_jpg: Optional[bytes] = None
    latest_jpg_id: int = 0
    active: bool = True
    last_sent_id: int = -1  # Track what client has received


class MjpegEncoder:
    """
    Low-latency MJPEG encoder with per-client buffering.
    
    This encoder:
    - Runs in a single thread (not one per client)
    - Encodes frames at preview resolution
    - Stores only the latest JPEG per client (ringbuffer size=1)
    - Uses latest-frame drop policy
    """
    
    def __init__(
        self,
        camera_manager,
        preview_width: int = 960,
        preview_height: int = 540,
        jpeg_quality: int = 75,
        target_fps: int = 30,
    ):
        self.camera = camera_manager
        self.preview_width = preview_width
        self.preview_height = preview_height
        self.jpeg_quality = jpeg_quality
        self.target_fps = target_fps
        
        # Client management
        self._clients: Dict[int, MjpegClient] = {}
        self._clients_lock = threading.Lock()
        self._next_client_id = 0
        
        # Encoder thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Stats
        self._frames_encoded = 0
        self._encode_errors = 0
        
        logger.info(
            f"MjpegEncoder init: {preview_width}x{preview_height} "
            f"@ Q{jpeg_quality}"
        )
    
    def start(self) -> None:
        """Start the encoder thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Encoder already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._encode_loop,
            name="MjpegEncoderThread",
            daemon=True
        )
        self._thread.start()
        logger.info("MjpegEncoder started")
    
    def stop(self) -> None:
        """Stop the encoder thread."""
        logger.info("Stopping MjpegEncoder...")
        
        self._stop_event.set()
        
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("Encoder thread did not stop within timeout")
                self._thread.join(timeout=0)
            self._thread = None
        
        logger.info(
            f"MjpegEncoder stopped: {self._frames_encoded} frames encoded, "
            f"{self._encode_errors} errors"
        )
    
    def add_client(self) -> MjpegClient:
        """Add a new client connection."""
        with self._clients_lock:
            client = MjpegClient(client_id=self._next_client_id)
            self._clients[client.client_id] = client
            self._next_client_id += 1
            logger.debug(f"Client {client.client_id} connected ({len(self._clients)} total)")
            return client
    
    def remove_client(self, client_id: int) -> None:
        """Remove a client connection."""
        with self._clients_lock:
            if client_id in self._clients:
                self._clients[client_id].active = False
                del self._clients[client_id]
                logger.debug(f"Client {client_id} disconnected ({len(self._clients)} remaining)")
    
    def get_client(self, client_id: int) -> Optional[MjpegClient]:
        """Get a client by ID."""
        with self._clients_lock:
            return self._clients.get(client_id)
    
    def _encode_loop(self) -> None:
        """
        Main encoding loop - runs in dedicated thread.
        
        This is the ONLY place where JPEG encoding happens.
        All clients share the same encoded frames.
        """
        logger.info("Encode loop started")
        
        last_encoded_id = -1
        frame_interval = 1.0 / self.target_fps if self.target_fps > 0 else 0.033
        
        while not self._stop_event.is_set():
            loop_start = time.time()
            
            # Get latest frame from camera
            frame_id, full_frame = self.camera.get_latest_frame()
            
            if full_frame is None:
                time.sleep(0.01)
                continue
            
            # Skip if we already encoded this frame
            if frame_id == last_encoded_id:
                # Small sleep to prevent CPU spinning
                sleep_time = frame_interval - (time.time() - loop_start)
                if sleep_time > 0:
                    time.sleep(min(sleep_time, 0.005))
                continue
            
            # Resize to preview resolution (KEY OPTIMIZATION)
            try:
                preview = cv2.resize(
                    full_frame,
                    (self.preview_width, self.preview_height),
                    interpolation=cv2.INTER_LINEAR
                )
            except Exception as e:
                logger.error(f"Resize failed: {e}")
                self._encode_errors += 1
                continue
            
            # Encode to JPEG
            try:
                ok, buf = cv2.imencode(
                    '.jpg',
                    preview,
                    [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
                )
            except Exception as e:
                logger.error(f"JPEG encode failed: {e}")
                self._encode_errors += 1
                continue
            
            if not ok:
                logger.warning("JPEG encoding returned False")
                self._encode_errors += 1
                continue
            
            jpg_bytes = buf.tobytes()
            last_encoded_id = frame_id
            self._frames_encoded += 1
            
            # Broadcast to all active clients (replace = size 1 buffer)
            with self._clients_lock:
                for client in self._clients.values():
                    if client.active:
                        client.latest_jpg = jpg_bytes
                        client.latest_jpg_id = frame_id
            
            # Maintain target FPS
            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        logger.info("Encode loop exited")
    
    @property
    def client_count(self) -> int:
        """Get number of active clients."""
        with self._clients_lock:
            return sum(1 for c in self._clients.values() if c.active)


# Global encoder instance
_encoder: Optional[MjpegEncoder] = None


def get_mjpeg_encoder() -> Optional[MjpegEncoder]:
    """Get the global MJPEG encoder."""
    return _encoder


def set_mjpeg_encoder(encoder: MjpegEncoder) -> None:
    """Set the global MJPEG encoder."""
    global _encoder
    _encoder = encoder
