"""Depth preprocessing: camera-space BGR frame -> model input tensor.

Follows the DAV2 "DPTImageProcessor" contract exactly (preprocessor_config.json
of the -hf checkpoint):
  - RGB conversion
  - keep-aspect-ratio resize so the LONGEST edge == `longest_edge` (518)
  - both H/W then rounded to a multiple of 14 (ensure_multiple_of)
  - rescale 1/255, normalize with ImageNet mean/std

All geometry downstream stays in CAMERA space; this module only produces the
model input plus the metadata needed to map the depth output back to camera
space (resized H/W and original H/W).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import torch

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass(frozen=True)
class DepthPreprocess:
    """Model input + bookkeeping to map depth back to camera space."""

    pixel_values: torch.Tensor  # (1, 3, h_model, w_model) float32, normalized
    orig_h: int
    orig_w: int
    resized_h: int  # model input height (multiple of 14, cropped area)
    resized_w: int  # model input width


def _target_size(h: int, w: int, longest_edge: int, multiple_of: int = 14) -> tuple[int, int]:
    """Keep aspect ratio: longest edge -> longest_edge, then snap to multiple_of."""
    scale = longest_edge / float(max(h, w))
    new_h = int(round(h * scale))
    new_w = int(round(w * scale))
    new_h = max(multiple_of, (new_h // multiple_of) * multiple_of)
    new_w = max(multiple_of, (new_w // multiple_of) * multiple_of)
    return new_h, new_w


def preprocess_bgr(
    frame_bgr: np.ndarray,
    longest_edge: int = 518,
    device: str | torch.device = "cuda",
) -> DepthPreprocess:
    """BGR uint8 HxWx3 camera frame -> normalized model tensor on `device`."""
    if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError(f"expected BGR HxWx3, got shape {frame_bgr.shape}")
    h, w = frame_bgr.shape[:2]
    new_h, new_w = _target_size(h, w, longest_edge)

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)

    x = rgb.astype(np.float32) / 255.0
    x = (x - IMAGENET_MEAN) / IMAGENET_STD
    tensor = torch.from_numpy(x).permute(2, 0, 1).unsqueeze(0).to(device, non_blocking=True)
    return DepthPreprocess(
        pixel_values=tensor, orig_h=h, orig_w=w, resized_h=new_h, resized_w=new_w
    )
