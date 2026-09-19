"""Exclusive camera occupancy for Visual Hub modules."""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Tuple

from .models import CameraSlot, OccupancyLease


class OccupancyError(Exception):
    def __init__(self, message: str, code: str = "CAMERA_BUSY") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class OccupancyManager:
    """One exclusive holder per camera slot / device path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_slot: Dict[str, OccupancyLease] = {}
        self._by_device: Dict[str, OccupancyLease] = {}

    def snapshot(self) -> List[Dict]:
        with self._lock:
            return [lease.to_dict() for lease in self._by_slot.values()]

    def holder_for(self, slot: str) -> Optional[str]:
        with self._lock:
            lease = self._by_slot.get(slot)
            return lease.holder if lease else None

    def acquire(self, slot: str, device: str, holder: str) -> OccupancyLease:
        device_key = device or f"slot:{slot}"
        with self._lock:
            existing_slot = self._by_slot.get(slot)
            if existing_slot and existing_slot.holder != holder:
                raise OccupancyError(
                    f"Camera slot {slot} is held by {existing_slot.holder}",
                    "CAMERA_BUSY",
                )
            existing_device = self._by_device.get(device_key)
            if existing_device and existing_device.holder != holder:
                raise OccupancyError(
                    f"Camera {device_key} is held by {existing_device.holder}",
                    "CAMERA_BUSY",
                )
            lease = OccupancyLease(
                slot=slot,
                device=device_key,
                holder=holder,
                since=time.time(),
                exclusive=True,
            )
            self._by_slot[slot] = lease
            self._by_device[device_key] = lease
            return lease

    def release(self, holder: str) -> None:
        with self._lock:
            slots = [slot for slot, lease in self._by_slot.items() if lease.holder == holder]
            devices = [dev for dev, lease in self._by_device.items() if lease.holder == holder]
            for slot in slots:
                self._by_slot.pop(slot, None)
            for dev in devices:
                self._by_device.pop(dev, None)

    def check_available(self, slot: str, device: str, holder: str) -> Tuple[bool, Optional[str]]:
        device_key = device or f"slot:{slot}"
        with self._lock:
            existing_slot = self._by_slot.get(slot)
            if existing_slot and existing_slot.holder != holder:
                return False, existing_slot.holder
            existing_device = self._by_device.get(device_key)
            if existing_device and existing_device.holder != holder:
                return False, existing_device.holder
            return True, None
