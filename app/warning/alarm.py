"""Alarm output interface (§34).

AlarmController maps the risk level to audible patterns and offers a GPIO
hook for a physical buzzer, plus a USB serial alarm-light mode.

  SAFE         : silent
  WARNING      : beep ---- beep ---- beep   (0.6s period)
  DANGER       : beep-beep-beep-beep        (0.25s period)
  SYSTEM ERROR : continuous long beep       (fail loud)

Modes:
  bell   : software buzzer (writes BEL to console; works on a TTY)
  gpio   : run a configurable shell command (e.g. `gpioset ...=1`)
  serial : USB tricolor alarm light (CH341 /dev/ttyUSB0) — 4-byte frames,
           DANGER flashes red + buzzes
  none   : silent

The serial light controls itself (flash/buzzer are on-device modes), so a
single frame per level change is enough — no software pattern thread.
"""
from __future__ import annotations

import os
import subprocess
import sys
import termios
import threading
import time

PATTERNS = {
    "SAFE":         (0.0, 0.0, 0),
    "WARNING":      (0.15, 0.60, 0),   # on_s, period_s, extra
    "DANGER":       (0.08, 0.25, 0),
    "SYSTEM ERROR": (0.60, 1.20, 0),
}

# USB 三色报警灯 4 字节指令（迪昆 V2.2）：头 A0 + 组 + 模式 + 校验(=前三码之和)
SERIAL_OFF = bytes([0xA0, 0x00, 0x00, 0xA0])               # 关闭全部
SERIAL_DANGER_FLASH = bytes([0xA0, 0x03, 0x02, 0xA5])       # 红灯闪烁（无蜂鸣）
SERIAL_DANGER_FLASH_BUZZ = bytes([0xA0, 0x07, 0x02, 0xA9])  # 红灯闪烁+蜂鸣


class AlarmController:
    def __init__(self, mode: str = "bell", gpio_cmd: str = "",
                 serial_port: str = "/dev/serial/by-id/usb-1a86_5523-if00-port0",
                 serial_baud: int = 9600,
                 serial_buzzer: bool = False) -> None:
        self.mode = mode          # none | bell | gpio | serial
        self.gpio_cmd = gpio_cmd  # e.g. "gpioset 1 3=1"
        self.serial_port = serial_port
        self.serial_baud = serial_baud
        self.serial_buzzer = serial_buzzer
        self._danger_frame = (SERIAL_DANGER_FLASH_BUZZ
                              if serial_buzzer else SERIAL_DANGER_FLASH)
        self._sfd = None
        self._level = "SAFE"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_level(self, level: str) -> None:
        if level not in PATTERNS:
            level = "SAFE"
        if level == self._level:
            return
        self._level = level
        if self.mode == "serial":
            frame = self._danger_frame if level == "DANGER" else SERIAL_OFF
            self._serial_send(frame)
            return
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1.0)
        if level == "SAFE" or self.mode == "none":
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, args=(level,), daemon=True)
        self._thread.start()

    # -- serial alarm light --------------------------------------------------
    def _serial_open(self) -> int:
        fd = os.open(self.serial_port, os.O_RDWR | os.O_NOCTTY)
        a = termios.tcgetattr(fd)
        # 8N1, raw
        a[2] = (a[2] & ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)) | termios.CS8
        a[3] = a[3] & ~(termios.ICANON | termios.ECHO | termios.ISIG)
        a[4] = a[4] & ~(termios.OPOST)
        baud = getattr(termios, "B%d" % self.serial_baud, termios.B9600)
        a[4] = (a[4] & ~termios.CBAUD) | baud
        a[5] = baud
        termios.tcsetattr(fd, termios.TCSANOW, a)
        return fd

    def _serial_send(self, frame: bytes) -> None:
        try:
            if self._sfd is None:
                self._sfd = self._serial_open()
            os.write(self._sfd, frame)
        except Exception:
            try:
                if self._sfd is not None:
                    os.close(self._sfd)
            except Exception:
                pass
            self._sfd = None

    def set_buzzer(self, enabled: bool) -> None:
        self.serial_buzzer = bool(enabled)
        self._danger_frame = (SERIAL_DANGER_FLASH_BUZZ
                              if self.serial_buzzer else SERIAL_DANGER_FLASH)
        if self.mode == "serial" and self._level == "DANGER":
            self._serial_send(self._danger_frame)   # 正在告警时立即切换

    # -- pattern runner -------------------------------------------------------
    def _beep(self) -> None:
        if self.mode == "gpio" and self.gpio_cmd:
            try:
                subprocess.run(self.gpio_cmd, shell=True, timeout=0.2)
            except Exception:
                pass
        else:
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except Exception:
                pass

    def _loop(self, level: str) -> None:
        on_s, period_s, _ = PATTERNS[level]
        while not self._stop.is_set():
            self._beep()
            self._stop.wait(max(0.001, on_s))
            if self._stop.is_set():
                break
            time.sleep(max(0.05, period_s - on_s))

    def stop(self) -> None:
        if self.mode == "serial":
            if self._sfd is not None:
                try:
                    os.write(self._sfd, SERIAL_OFF)
                    os.close(self._sfd)
                except Exception:
                    pass
                self._sfd = None
            self._level = "SAFE"
            return
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1.0)
            self._thread = None
        self._level = "SAFE"