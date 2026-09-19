"""Experimental PPE detector backed by a local TensorRT YOLO engine.

The detector deliberately does not import Ultralytics.  The community model is
an experimental artifact, so callers must verify the manifest before an engine
is built and must surface ``UNAVAILABLE`` when it cannot be loaded.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

import cv2
import numpy as np

from app.warning.person_detection import IoUTracker, PersonDetection

# Read from the pinned ONNX metadata: {0: head, 1: helmet, 2: person}.
CLASSES = ("head", "helmet", "person")


class PpeUnavailable(RuntimeError):
    """Raised when the explicitly configured PPE engine cannot run."""


@dataclass(frozen=True)
class PpeDetection:
    label: str
    bbox: tuple[int, int, int, int]
    confidence: float
    track_id: int = -1


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0, x1, y1 = max(ax, bx), max(ay, by), min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return float(inter / union) if union > 0 else 0.0


def _nms(items: Sequence[PpeDetection], threshold: float) -> list[PpeDetection]:
    kept: list[PpeDetection] = []
    for item in sorted(items, key=lambda value: value.confidence, reverse=True):
        if all(_iou(item.bbox, old.bbox) < threshold for old in kept):
            kept.append(item)
    return kept


def decode_yolov8(output: np.ndarray, frame_shape: tuple[int, int],
                  input_size: int, confidence: float, iou: float) -> list[PpeDetection]:
    """Decode the standard YOLOv8 ``[1, 4 + classes, anchors]`` output."""
    values = np.asarray(output)
    if values.ndim == 3:
        values = values[0]
    if values.ndim != 2:
        raise PpeUnavailable("unexpected PPE output rank %s" % (values.ndim,))
    if values.shape[0] == 4 + len(CLASSES):
        values = values.T
    if values.shape[1] < 4 + len(CLASSES):
        raise PpeUnavailable("unexpected PPE output shape %s" % (values.shape,))
    h, w = frame_shape
    sx, sy = float(w) / input_size, float(h) / input_size
    by_label: dict[str, list[PpeDetection]] = {label: [] for label in CLASSES}
    for row in values:
        class_id = int(np.argmax(row[4:4 + len(CLASSES)]))
        score = float(row[4 + class_id])
        if score < confidence:
            continue
        cx, cy, bw, bh = [float(v) for v in row[:4]]
        x0, y0 = (cx - bw / 2.0) * sx, (cy - bh / 2.0) * sy
        box = (max(0, int(round(x0))), max(0, int(round(y0))),
               max(1, int(round(bw * sx))), max(1, int(round(bh * sy))))
        by_label[CLASSES[class_id]].append(PpeDetection(CLASSES[class_id], box, score))
    return [item for label in CLASSES for item in _nms(by_label[label], iou)]


def associate_ppe(detections: Iterable[PpeDetection], tracker: IoUTracker,
                  min_overlap: float = 0.15
                  ) -> list[tuple[PersonDetection, list[PpeDetection], list[PpeDetection]]]:
    """Associate helmet/head detections with the top third of each person box."""
    items = list(detections)
    persons = [item for item in items if item.label == "person"]
    tracked = tracker.update([PersonDetection(item.bbox, item.confidence) for item in persons])
    result = []
    for person in tracked:
        x, y, w, h = person.bbox
        head_region = (x, y, w, max(1, int(round(h * 0.42))))
        helmets, heads = [], []
        for item in items:
            if item.label not in ("helmet", "head"):
                continue
            if _iou(item.bbox, head_region) >= min_overlap:
                (helmets if item.label == "helmet" else heads).append(item)
        result.append((person, helmets, heads))
    return result


class ModelHelmetEngine:
    """Convert a PPE model's three classes into stable per-person verdicts."""

    def __init__(self, min_overlap: float = 0.15, on_event=None) -> None:
        self.min_overlap, self.on_event = float(min_overlap), on_event
        self.reset()

    def reset(self) -> None:
        self._tracker = IoUTracker()
        self._head_tracker = IoUTracker()
        self._last: dict[int, str] = {}

    def update(self, _frame_bgr: np.ndarray,
               detections: Sequence[PpeDetection]):
        # Imported lazily to keep this module free of a circular test dependency.
        from app.dms.helmet import HelmetVerdict
        verdicts = []
        groups = associate_ppe(detections, self._tracker, self.min_overlap)
        # Construction-trained models commonly miss a seated cabin ``person``
        # but still detect the driver's exposed head.  A qualifying head is a
        # legitimate cabin anchor; do not discard it merely because its body
        # class is out of domain.  A matching helmet overrides it.
        if not groups:
            items = list(detections)
            helmets_all = [item for item in items if item.label == "helmet"]
            head_tracks = self._head_tracker.update([
                PersonDetection(item.bbox, item.confidence)
                for item in items if item.label == "head"
            ])
            groups = []
            for head in head_tracks:
                helmets = [item for item in helmets_all
                           if _iou(item.bbox, head.bbox) >= self.min_overlap]
                groups.append((head, helmets, [
                    PpeDetection("head", head.bbox, head.confidence, head.track_id)
                ]))
        for person, helmets, heads in groups:
            if helmets:
                verdict, reason, score = "worn", "model_helmet", max(x.confidence for x in helmets)
            elif heads:
                verdict, reason, score = "not_worn", "model_exposed_head", max(x.confidence for x in heads)
            else:
                verdict, reason, score = "unknown", "model_no_head_evidence", 0.0
            previous = self._last.get(person.track_id)
            self._last[person.track_id] = verdict
            if previous != verdict and self.on_event is not None:
                self.on_event("helmet_verdict", {
                    "track_id": person.track_id, "verdict": verdict,
                    "reason": reason, "bbox": list(person.bbox),
                    "model_score": round(score, 4), "experimental": True,
                })
            verdicts.append(HelmetVerdict(
                track_id=person.track_id, bbox=person.bbox, head_roi=person.bbox,
                verdict=verdict, reason=reason, helmet_color_ratio=0.0,
                skin_ratio=0.0, dark_ratio=0.0,
                conf="high" if score >= 0.70 else "mid" if score >= 0.45 else "low",
                age_frames=0))
        return verdicts


class TensorRTPpeDetector:
    """Static-shape TensorRT YOLOv8 runner using the existing Torch CUDA context."""

    def __init__(self, engine_path: str, input_size: int = 640,
                 confidence: float = 0.45, iou: float = 0.50) -> None:
        if not os.path.isfile(engine_path):
            raise PpeUnavailable("PPE TensorRT engine missing: %s" % engine_path)
        try:
            import tensorrt as trt
            import torch
        except ImportError as exc:
            raise PpeUnavailable("TensorRT runtime unavailable: %s" % exc) from exc
        if not torch.cuda.is_available():
            raise PpeUnavailable("CUDA is unavailable for PPE TensorRT engine")
        self._trt, self._torch = trt, torch
        self.input_size, self.confidence, self.iou = int(input_size), float(confidence), float(iou)
        logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as handle, trt.Runtime(logger) as runtime:
            self._engine = runtime.deserialize_cuda_engine(handle.read())
        if self._engine is None:
            raise PpeUnavailable("cannot deserialize PPE TensorRT engine")
        self._context = self._engine.create_execution_context()
        self._input_index = next((i for i in range(self._engine.num_bindings)
                                  if self._engine.binding_is_input(i)), None)
        self._output_index = next((i for i in range(self._engine.num_bindings)
                                   if not self._engine.binding_is_input(i)), None)
        if self._input_index is None or self._output_index is None:
            raise PpeUnavailable("PPE engine must have one input and one output")
        shape = tuple(self._engine.get_binding_shape(self._input_index))
        if any(dim < 0 for dim in shape):
            shape = (1, 3, self.input_size, self.input_size)
            self._context.set_binding_shape(self._input_index, shape)
        self._input = torch.empty(shape, dtype=torch.float32, device="cuda")
        output_shape = tuple(self._context.get_binding_shape(self._output_index))
        self._output = torch.empty(output_shape, dtype=torch.float32, device="cuda")
        self.name = "TensorRT-PPE:%s" % os.path.basename(engine_path)
        self.last_ms = 0.0

    def infer(self, frame_bgr: np.ndarray) -> list[PpeDetection]:
        started = time.perf_counter()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (self.input_size, self.input_size), interpolation=cv2.INTER_LINEAR)
        host = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
        self._input.copy_(self._torch.from_numpy(host), non_blocking=True)
        bindings = [0] * self._engine.num_bindings
        bindings[self._input_index] = int(self._input.data_ptr())
        bindings[self._output_index] = int(self._output.data_ptr())
        if not self._context.execute_v2(bindings):
            raise PpeUnavailable("PPE TensorRT execution failed")
        self._torch.cuda.synchronize()
        self.last_ms = (time.perf_counter() - started) * 1000.0
        return decode_yolov8(self._output.detach().cpu().numpy(), frame_bgr.shape[:2],
                             self.input_size, self.confidence, self.iou)
