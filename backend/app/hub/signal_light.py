"""Single-owner controller for the shared USB three-colour signal light.

The rear warning and cabin detector share one physical CH341 serial device.
Keeping arbitration here prevents two child processes from overwriting one
another's light pattern. Rear collision danger always takes precedence.
"""
from __future__ import annotations

import os
import termios
import threading
from typing import Callable, Optional


# 迪昆 USB 串口三色报警灯 V2.2: checksum is the sum of the first 3 bytes.
ALL_OFF = bytes((0xA0, 0x00, 0x00, 0xA0))
YELLOW_FLASH = bytes((0xA0, 0x01, 0x02, 0xA3))
RED_FLASH = bytes((0xA0, 0x03, 0x02, 0xA5))
BUZZER_INTERMITTENT = bytes((0xA0, 0x04, 0x02, 0xA6))
RED_FLASH_BUZZER = bytes((0xA0, 0x07, 0x02, 0xA9))

FATIGUE_STATES = {"DROWSY_WARN", "WARN", "WARNING", "DROWSY_ALARM", "ALARM"}
REAR_DANGER_STATES = {"DANGER", "SYSTEM_ERROR", "SYSTEM ERROR"}


class SignalLightController:
    """Resolve shared alarm state and write only on a resolved transition."""

    def __init__(
        self,
        port: str = "/dev/serial/by-id/usb-1a86_5523-if00-port0",
        baud: int = 9600,
        writer: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self._writer = writer
        self._fd: Optional[int] = None
        self._lock = threading.RLock()
        self._rear_level = "SAFE"
        self._rear_buzzer = False
        self._fatigue_state = "NORMAL"
        self._fatigue_buzzer = False
        self._resolved: tuple[str, bool] = ("off", False)
        self.last_error: Optional[str] = None

    def update_rear(self, level: str, buzzer: bool = False) -> None:
        with self._lock:
            self._rear_level = str(level or "SAFE").upper()
            self._rear_buzzer = bool(buzzer)
            self._apply_locked()

    def update_fatigue(self, state: str, buzzer: bool = False) -> None:
        with self._lock:
            self._fatigue_state = str(state or "NORMAL").upper()
            self._fatigue_buzzer = bool(buzzer)
            self._apply_locked()

    def clear_rear(self) -> None:
        self.update_rear("SAFE", False)

    def clear_fatigue(self) -> None:
        self.update_fatigue("NORMAL", False)

    def snapshot(self) -> dict:
        with self._lock:
            mode, buzzer = self._resolved
            return {
                "mode": mode,
                "buzzer": buzzer,
                "rear_level": self._rear_level,
                "fatigue_state": self._fatigue_state,
                "last_error": self.last_error,
            }

    def stop(self) -> None:
        with self._lock:
            self._rear_level = "SAFE"
            self._fatigue_state = "NORMAL"
            self._rear_buzzer = False
            self._fatigue_buzzer = False
            self._send(ALL_OFF)
            self._resolved = ("off", False)
            self._close()

    def _desired(self) -> tuple[str, bool]:
        # Rear collision danger wins over every cabin indication.
        if self._rear_level in REAR_DANGER_STATES:
            return ("rear", self._rear_buzzer)
        if self._fatigue_state in FATIGUE_STATES:
            return ("fatigue", self._fatigue_buzzer)
        return ("off", False)

    def _apply_locked(self) -> None:
        desired = self._desired()
        if desired == self._resolved:
            return
        # Reset device modes before selecting a new colour/buzzer pattern.
        self._send(ALL_OFF)
        mode, buzzer = desired
        if mode == "rear":
            self._send(RED_FLASH_BUZZER if buzzer else RED_FLASH)
        elif mode == "fatigue":
            self._send(YELLOW_FLASH)
            if buzzer:
                self._send(BUZZER_INTERMITTENT)
        self._resolved = desired

    def _open(self) -> int:
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY)
        attrs = termios.tcgetattr(fd)
        attrs[2] = (attrs[2] & ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)) | termios.CS8
        attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
        attrs[1] &= ~termios.OPOST
        baud = getattr(termios, f"B{self.baud}", termios.B9600)
        attrs[4] = baud
        attrs[5] = baud
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        return fd

    def _send(self, frame: bytes) -> None:
        try:
            if self._writer is not None:
                self._writer(frame)
            else:
                if self._fd is None:
                    self._fd = self._open()
                os.write(self._fd, frame)
            self.last_error = None
        except Exception as exc:  # Serial hardware cannot take the hub down.
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._close()

    def _close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
