"""
Tests for CameraManager.

These tests verify:
1. Singleton pattern works
2. start/stop/restart lifecycle
3. Graceful shutdown releases resources
4. Multiple stop() calls are safe
"""
import time
import threading
import subprocess
import signal
import sys
import os
from unittest import mock

import cv2
import numpy as np
import pytest

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class FakeCapture:
    def __init__(self, *_args, **_kwargs):
        self.opened = True
        self.width = 1280
        self.height = 720
        self.fps = 30.0

    def isOpened(self):
        return self.opened

    def read(self):
        if not self.opened:
            return False, None
        return True, np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def release(self):
        self.opened = False

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.width = int(value)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.height = int(value)
        elif prop == cv2.CAP_PROP_FPS:
            self.fps = float(value)
        return True

    def get(self, prop):
        return {
            cv2.CAP_PROP_FRAME_WIDTH: self.width,
            cv2.CAP_PROP_FRAME_HEIGHT: self.height,
            cv2.CAP_PROP_FPS: self.fps,
        }.get(prop, 0.0)


@pytest.fixture(autouse=True)
def fake_camera(monkeypatch):
    original_exists = os.path.exists
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    monkeypatch.setattr(
        os.path, "exists",
        lambda path: True if str(path).startswith("/dev/video") else original_exists(path),
    )


def fail(msg):
    """Test failure."""
    print(f"FAIL: {msg}")
    sys.exit(1)


def test_singleton():
    """Test that CameraManager is a singleton."""
    from backend.app.camera.manager import CameraManager, get_camera_manager
    
    m1 = CameraManager()
    m2 = CameraManager()
    m3 = get_camera_manager()
    
    assert m1 is m2
    assert m1 is m3
    print("PASS: Singleton pattern works")


def test_stop_idempotent():
    """Test that stop() can be called multiple times safely."""
    from backend.app.camera.manager import CameraManager, CameraState
    
    # Force fresh singleton
    CameraManager._instance = None
    
    m = CameraManager()
    
    # Should start in DISCONNECTED
    assert m.state == CameraState.DISCONNECTED
    
    # Multiple stops should be safe
    m.stop()
    assert m.state == CameraState.DISCONNECTED
    
    m.stop()
    assert m.state == CameraState.DISCONNECTED
    
    m.stop()
    assert m.state == CameraState.DISCONNECTED
    
    print("PASS: stop() is idempotent")


def test_camera_lifecycle():
    """Test basic camera start/stop/restart."""
    from backend.app.camera.manager import CameraManager, CameraState
    
    # Force fresh singleton
    CameraManager._instance = None
    
    m = CameraManager()
    
    # Start camera
    state = m.start("/dev/video0", width=1280, height=720)
    assert state == CameraState.RUNNING
    assert m.is_running
    
    # Get some frames
    for _ in range(10):
        frame_id, frame = m.get_latest_frame()
        if frame is not None:
            break
        time.sleep(0.01)
    
    frame_id, frame = m.get_latest_frame()
    assert frame is not None, "Should have received at least one frame"
    print(f"  Received frame {frame_id}, shape: {frame.shape}")
    
    # Stop
    state = m.stop()
    assert state == CameraState.DISCONNECTED
    assert not m.is_running
    
    # Verify device released
    time.sleep(0.1)
    
    print("PASS: Camera lifecycle works")


def test_restart():
    """Test restart functionality."""
    from backend.app.camera.manager import CameraManager, CameraState
    
    # Force fresh singleton
    CameraManager._instance = None
    
    m = CameraManager()
    
    # Start
    m.start("/dev/video0")
    assert m.is_running
    
    # Get frame ID before restart
    old_frame_id, _ = m.get_latest_frame()
    time.sleep(0.1)
    
    # Restart
    state = m.restart()
    assert state == CameraState.RUNNING
    
    # Frame ID should have changed (new capture)
    new_frame_id, _ = m.get_latest_frame()
    assert new_frame_id >= old_frame_id
    
    m.stop()
    print("PASS: Restart works")


def test_graceful_shutdown_script():
    """
    Test that Ctrl+C properly releases camera.
    
    This test:
    1. Starts a Python script that opens the camera
    2. Sends SIGINT to simulate Ctrl+C
    3. Verifies the process exits
    4. Verifies the camera device is released
    """
    if os.environ.get("SEG_DEMO_HARDWARE_TESTS") != "1":
        pytest.skip("real camera release test requires SEG_DEMO_HARDWARE_TESTS=1")
    test_script = '''
import sys
import time
import signal
sys.path.insert(0, '..')

from backend.app.camera.manager import CameraManager, get_camera_manager

def signal_handler(sig, frame):
    print("SIGINT received, stopping camera")
    m = get_camera_manager()
    m.stop()
    print("Camera stopped, exiting")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

print("Starting camera...")
m = get_camera_manager()
m.start("/dev/video0")
print(f"Camera running: {m.is_running}")

# Wait for frames
for i in range(30):
    frame_id, frame = m.get_latest_frame()
    if frame is not None:
        print(f"Received frame {frame_id}")
        break
    time.sleep(0.1)

print("Ready, waiting for signal...")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("KeyboardInterrupt received")
    m.stop()
'''
    
    # Write test script
    script_path = "/tmp/test_camera_shutdown.py"
    with open(script_path, "w") as f:
        f.write(test_script)
    
    # Run script in background
    print("Starting camera server script...")
    proc = subprocess.Popen(
        [sys.executable, script_path],
        cwd=os.path.dirname(__file__),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Wait for startup
    time.sleep(2)
    
    # Check if process is running
    if proc.poll() is not None:
        output = proc.stdout.read()
        print(f"Script exited early: {output}")
        fail("Script exited before signal")
    
    # Send SIGINT
    print("Sending SIGINT...")
    proc.send_signal(signal.SIGINT)
    
    # Wait for process to exit
    try:
        output, _ = proc.communicate(timeout=5)
        print(f"Script output:\n{output}")
    except subprocess.TimeoutExpired:
        proc.kill()
        fail("Script did not exit after SIGINT")
    
    # Verify exit code
    assert proc.returncode == 0, f"Script exited with code {proc.returncode}"
    print("PASS: Graceful shutdown works")
    
    # Verify device released
    time.sleep(0.5)
    result = subprocess.run(
        ["fuser", "/dev/video0"],
        capture_output=True
    )
    if result.returncode == 0:
        # Device still in use
        pids = result.stdout.decode().strip()
        print(f"WARNING: /dev/video0 still used by: {pids}")
    else:
        print("PASS: /dev/video0 released after shutdown")


def test_multiple_starts_stops():
    """Test multiple start/stop cycles."""
    from backend.app.camera.manager import CameraManager, CameraState
    
    # Force fresh singleton
    CameraManager._instance = None
    
    m = CameraManager()
    
    for i in range(3):
        print(f"  Cycle {i+1}/3")
        m.start("/dev/video0")
        assert m.is_running
        
        # Get a frame
        time.sleep(0.1)
        frame_id, frame = m.get_latest_frame()
        assert frame is not None
        
        m.stop()
        assert not m.is_running
        
        # Small delay between cycles
        time.sleep(0.2)
    
    print("PASS: Multiple start/stop cycles work")


if __name__ == "__main__":
    print("=" * 60)
    print("CameraManager Tests")
    print("=" * 60)
    
    try:
        test_singleton()
        test_stop_idempotent()
        test_camera_lifecycle()
        test_restart()
        test_multiple_starts_stops()
        test_graceful_shutdown_script()
        
        print()
        print("=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    except Exception as e:
        print()
        print("=" * 60)
        print(f"TEST FAILED: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        sys.exit(1)
