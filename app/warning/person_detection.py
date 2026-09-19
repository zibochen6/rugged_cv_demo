"""YOLO person detection with a small dependency-independent tracker."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import numpy as np


def _ensure_tensorrt_numpy_compat() -> None:
    """Restore the one removed alias still used by TensorRT 8.5.

    JetPack 5.1.3 ships TensorRT bindings whose dtype table accesses
    np.bool while this project's NumPy no longer exposes that alias.
    np.bool_ is the identical scalar dtype TensorRT expects.
    """
    if "bool" not in np.__dict__:
        setattr(np, "bool", np.bool_)


@dataclass(frozen=True)
class PersonDetection:
    bbox: tuple[int, int, int, int]  # x, y, width, height
    confidence: float
    track_id: int = -1
    distance_m: float | None = None
    distance_valid: bool = False


def _iou(a: tuple[int, int, int, int],
         b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return float(inter / union) if union > 0 else 0.0


class IoUTracker:
    """Greedy IoU association; sufficient for warning-box continuity."""

    def __init__(self, iou_threshold: float = 0.25,
                 max_missed_frames: int = 8) -> None:
        self.iou_threshold = float(iou_threshold)
        self.max_missed_frames = int(max_missed_frames)
        self._tracks: dict[int, tuple[tuple[int, int, int, int], int]] = {}
        self._next_id = 1

    def update(self, detections: Iterable[PersonDetection]
               ) -> list[PersonDetection]:
        detections = list(detections)
        candidates = []
        for di, det in enumerate(detections):
            for tid, (box, missed) in self._tracks.items():
                candidates.append((_iou(det.bbox, box), di, tid))
        assigned_d, assigned_t = set(), set()
        matches: dict[int, int] = {}
        for score, di, tid in sorted(candidates, reverse=True):
            if score < self.iou_threshold:
                break
            if di not in assigned_d and tid not in assigned_t:
                matches[di] = tid
                assigned_d.add(di)
                assigned_t.add(tid)

        updated: dict[int, tuple[tuple[int, int, int, int], int]] = {}
        output = []
        for di, det in enumerate(detections):
            tid = matches.get(di)
            if tid is None:
                tid = self._next_id
                self._next_id += 1
            updated[tid] = (det.bbox, 0)
            output.append(replace(det, track_id=tid))
        for tid, (box, missed) in self._tracks.items():
            if tid in updated:
                continue
            missed += 1
            if missed <= self.max_missed_frames:
                updated[tid] = (box, missed)
        self._tracks = updated
        return output


class UltralyticsPersonDetector:
    """Load a YOLOv8 TensorRT engine through Ultralytics.

    The import is intentionally lazy so geometry/tests work without the
    optional runtime. Missing model or package is a visible unavailable state.
    """

    def __init__(self, model_path: str, confidence: float = 0.45,
                 iou: float = 0.5, imgsz: int = 640) -> None:
        _ensure_tensorrt_numpy_compat()
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"person model missing: {path}; run scripts/setup_person_detector.sh")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed; run scripts/setup_person_detector.sh"
            ) from exc
        self.model = YOLO(str(path), task="detect")
        self.confidence = float(confidence)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.tracker = IoUTracker()
        self.last_ms = 0.0
        self.name = f"YOLO-person:{path.name}"

    def infer(self, frame: np.ndarray) -> list[PersonDetection]:
        import time
        start = time.perf_counter()
        results = self.model.predict(
            source=frame, imgsz=self.imgsz, conf=self.confidence,
            iou=self.iou, classes=[0], verbose=False)
        detections: list[PersonDetection] = []
        if results:
            boxes = results[0].boxes
            if boxes is not None:
                xyxy = boxes.xyxy.detach().cpu().numpy()
                conf = boxes.conf.detach().cpu().numpy()
                cls = boxes.cls.detach().cpu().numpy()
                for coords, score, class_id in zip(xyxy, conf, cls):
                    if int(class_id) != 0:
                        continue
                    x0, y0, x1, y1 = [int(round(v)) for v in coords]
                    detections.append(PersonDetection(
                        (x0, y0, max(1, x1 - x0), max(1, y1 - y0)),
                        float(score)))
        self.last_ms = (time.perf_counter() - start) * 1000.0
        return self.tracker.update(detections)
