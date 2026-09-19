"""Haar face detection + ROI helpers for the DMS demo (offline, no new deps).

关键陷阱（契约 §1.2，已核实）：本机 OpenCV 4.5.4 是发行版构建，**没有 cv2.data**，
`cv2.data.haarcascades` 直接 AttributeError。因此这里自行解析级联目录
(`resolve_haar_dir()`)，并按候选顺序探测；找不到时抛出 HaarUnavailable，
由调用方打印单行可操作错误。

人脸跟踪复用 app/warning/person_detection.py 的 IoUTracker（只读 import，
不修改该文件）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from app.warning.person_detection import IoUTracker, PersonDetection

HAAR_CANDIDATES = (
    "/usr/share/opencv4/haarcascades",     # 本机实测命中
    "/usr/share/opencv/haarcascades",
    "/usr/local/share/opencv4/haarcascades",
)

FACE_XML = "haarcascade_frontalface_default.xml"
EYE_XML = "haarcascade_eye.xml"
SMILE_XML = "haarcascade_smile.xml"

Box = Tuple[int, int, int, int]            # x, y, w, h


class HaarUnavailable(Exception):
    """No usable Haar cascade directory (face xml missing everywhere)."""


def resolve_haar_dir() -> str:
    """env DMS_HAAR_DIR > HAAR_CANDIDATES（需含 face xml）；全失败 raise。"""
    candidates: List[str] = []
    env_dir = os.environ.get("DMS_HAAR_DIR", "").strip()
    if env_dir:
        candidates.append(env_dir)
    candidates.extend(HAAR_CANDIDATES)
    # 某些环境下 cv2.data 存在；守护式使用，绝不直接引用（契约 H2）
    data = getattr(cv2, "data", None)
    data_dir = getattr(data, "haarcascades", None) if data is not None else None
    if data_dir:
        candidates.append(str(data_dir))
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, FACE_XML)):
            return candidate
    raise HaarUnavailable(
        "no Haar cascade dir with %s (tried: %s); set DMS_HAAR_DIR"
        % (FACE_XML, ", ".join(candidates)))


def load_cascade(directory: str, name: str) -> Optional[cv2.CascadeClassifier]:
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        return None
    cascade = cv2.CascadeClassifier(path)
    return None if cascade.empty() else cascade


def crop_norm(gray: np.ndarray, box: Box,
              x_frac: Sequence[float],
              y_frac: Sequence[float]) -> Optional[Tuple[np.ndarray, Box]]:
    """ROI 用相对人脸框的归一化坐标裁剪；越界部分被夹回画面内。

    返回 (roi, (x, y, w, h))；退化为空 ROI 时返回 None（调用方必须容忍）。
    """
    if gray is None or gray.size == 0:
        return None
    height, width = gray.shape[:2]
    x, y, w, h = [int(v) for v in box]
    if w <= 0 or h <= 0:
        return None
    x0 = int(round(x + float(x_frac[0]) * w))
    x1 = int(round(x + float(x_frac[1]) * w))
    y0 = int(round(y + float(y_frac[0]) * h))
    y1 = int(round(y + float(y_frac[1]) * h))
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width, x1))
    y1 = max(y0 + 1, min(height, y1))
    roi = gray[y0:y1, x0:x1]
    if roi is None or roi.size == 0:
        return None
    return roi, (x0, y0, x1 - x0, y1 - y0)


def dark_ratio(roi: Optional[np.ndarray], threshold: float) -> Optional[float]:
    """ROI 内 `gray < threshold` 的像素占比（0.0–1.0）。

    这是**代理量**：光照敏感，不是 EAR、也不是 PERCLOS（见文档的诚实标注）。
    """
    if roi is None or roi.size == 0:
        return None
    return float(np.count_nonzero(roi < float(threshold))) / float(roi.size)


@dataclass(frozen=True)
class FaceTrack:
    box: Box
    track_id: int


class FaceDetector:
    """Haar frontal-face detector with IoU track ids (largest face wins)."""

    def __init__(self, haar_dir: Optional[str] = None,
                 scale_factor: float = 1.1, min_neighbors: int = 5,
                 min_size_px: int = 48, crosscheck: bool = False) -> None:
        directory = haar_dir or resolve_haar_dir()
        cascade = load_cascade(directory, FACE_XML)
        if cascade is None:
            raise HaarUnavailable(
                "cannot load %s from %s" % (FACE_XML, directory))
        self.haar_dir = directory
        self._face = cascade
        self.scale_factor = float(scale_factor)
        self.min_neighbors = int(min_neighbors)
        self.min_size_px = int(min_size_px)
        self._tracker = IoUTracker()
        self.crosscheck = bool(crosscheck)
        self._eye = load_cascade(directory, EYE_XML) if crosscheck else None
        self._smile = load_cascade(directory, SMILE_XML) if crosscheck else None

    def detect(self, gray: np.ndarray) -> List[Box]:
        if gray is None or gray.size == 0:
            return []
        min_size = (self.min_size_px, self.min_size_px)
        faces = self._face.detectMultiScale(
            gray, scaleFactor=self.scale_factor,
            minNeighbors=self.min_neighbors, minSize=min_size)
        # 注意：有检出时 cv2 返回的是 ndarray，`faces or ()` 会触发 numpy 的
        # "truth value of an array is ambiguous" —— 真机上有人脸时必崩（已实测），
        # 因此这里只用 len() 判空，绝不做布尔求值。
        if faces is None or len(faces) == 0:
            return []
        boxes: List[Box] = []
        for face in faces:
            x, y, w, h = [int(v) for v in face]
            if w > 0 and h > 0:
                boxes.append((x, y, w, h))
        return boxes

    def update(self, gray: np.ndarray) -> List[FaceTrack]:
        """Detect + associate track ids (greedy IoU, see IoUTracker)."""
        boxes = self.detect(gray)
        tracked = self._tracker.update(
            [PersonDetection(box, 1.0) for box in boxes])
        return [FaceTrack(det.bbox, det.track_id) for det in tracked]

    def main(self, tracks: Sequence[FaceTrack]) -> Optional[FaceTrack]:
        """Largest face by area (contract: only the main face is processed)."""
        if not tracks:
            return None
        return max(tracks, key=lambda t: int(t.box[2]) * int(t.box[3]))

    def cascade_hits(self, gray: np.ndarray, face_box: Box
                     ) -> Tuple[int, int]:
        """Optional eye/smile cascade hits (debug fields only, never scored)."""
        if self._eye is None and self._smile is None:
            return 0, 0
        x, y, w, h = face_box
        eye_hits = smile_hits = 0
        if self._eye is not None:
            eye_roi = gray[max(0, y):max(0, y + int(0.55 * h)),
                           max(0, x):max(0, x + w)]
            if eye_roi is not None and eye_roi.size > 0:
                found = self._eye.detectMultiScale(
                    eye_roi, scaleFactor=1.1, minNeighbors=6,
                    minSize=(12, 12))
                eye_hits = 0 if found is None else len(found)
        if self._smile is not None:
            mouth_roi = gray[max(0, y + int(0.55 * h)):max(0, y + h),
                             max(0, x):max(0, x + w)]
            if mouth_roi is not None and mouth_roi.size > 0:
                found = self._smile.detectMultiScale(
                    mouth_roi, scaleFactor=1.7, minNeighbors=20,
                    minSize=(20, 20))
                smile_hits = 0 if found is None else len(found)
        return int(eye_hits), int(smile_hits)

    def reset(self) -> None:
        self._tracker = IoUTracker()