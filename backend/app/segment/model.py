"""EfficientTAM camera-predictor wrapper (single object, streaming).

A trimmed copy of the real-time fork (GPIOX/EfficientTAM_real_time) is
vendored under third_party/efficient_track_anything so the feature is
self-contained (the repo-level third_party/ is git-ignored). This wrapper
installs a pure-numpy fallback for the compiled connected-components
extension (only reachable when hole filling is enabled, which this minimal
build keeps off — the shim is insurance) and exposes the tiny API the
service needs:

    load / reset / select / add_point / track
"""
import logging
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from . import config as seg_cfg

logger = logging.getLogger(__name__)

# The in-repo vendored package wins over the venv egg-link (which points at
# <repo>/third_party/EfficientTAM): prepending keeps the tracker versioned
# together with this wrapper.
_THIRD_PARTY = seg_cfg.THIRD_PARTY_DIR
if str(_THIRD_PARTY) not in sys.path:
    sys.path.insert(0, str(_THIRD_PARTY))

import torch  # noqa: E402  (torch lives in the app venv)


# ---------------------------------------------------------------------------
# Pure-numpy fallback for efficient_track_anything._C (compiled CUDA ext).
# The streaming path never calls it (fill_hole_area stays 0); this shim only
# exists so an accidental call cannot crash the studio.
# ---------------------------------------------------------------------------
_NC_MISSING = object()


class _ConnectedComponentsShim:
    """Minimal numpy 8-connectivity labelling, same call contract as _C."""

    @staticmethod
    def get_connected_componnets(mask):
        # mask: (N, 1, H, W) uint8 CUDA tensor -> (labels, counts)
        arr = mask.cpu().numpy()
        n = arr.shape[0]
        h, w = arr.shape[2], arr.shape[3]
        labels_out = np.zeros((n, h, w), dtype=np.int32)
        counts_out = []
        for i in range(n):
            fg = arr[i, 0] > 0
            lab = np.zeros((h, w), dtype=np.int32)
            cur = 0
            # BFS over foreground pixels (8-connectivity)
            ys, xs = np.nonzero(fg)
            order = np.lexsort((xs, ys))
            for idx in order:
                y, x = int(ys[idx]), int(xs[idx])
                if lab[y, x] != 0:
                    continue
                cur += 1
                lab[y, x] = cur
                stack = [(y, x)]
                while stack:
                    cy, cx = stack.pop()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w and fg[ny, nx] and lab[ny, nx] == 0:
                                lab[ny, nx] = cur
                                stack.append((ny, nx))
            counts_out.append(lab.max())
            labels_out[i] = lab
        counts = np.zeros((n, int(max(counts_out)) + 1 if counts_out else 1), dtype=np.int64)
        for i in range(n):
            lab = labels_out[i]
            flat = lab.reshape(-1)
            for c in range(1, int(lab.max()) + 1):
                counts[i, c] = int((flat == c).sum())
        return torch.from_numpy(labels_out), torch.from_numpy(counts)


def install_connected_components_fallback() -> None:
    pkg = __import__("efficient_track_anything", fromlist=["_C"])
    ext = getattr(pkg, "_C", _NC_MISSING)
    if ext is _NC_MISSING or ext is None:
        try:
            from efficient_track_anything import _C  # may exist if compiled
        except Exception:  # pragma: no cover - normal path on Jetson
            setattr(pkg, "_C", _ConnectedComponentsShim)
        else:
            if _C is None:  # type: ignore[name-defined]  # pragma: no cover
                setattr(pkg, "_C", _ConnectedComponentsShim)


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------
class EfficientTAMSegmentModel:
    """Thin wrapper around EfficientTAMCameraPredictor.

    The model works on one fixed input resolution (perpare_data stretches to
    `image_size` square); masks come back at the size of the frame we feed
    in. We always feed the same downscaled frame size, so masks are
    guaranteed to be at that size.
    """

    def __init__(
        self,
        config_name: str = seg_cfg.MODEL_CONFIG,
        checkpoint: Optional[Path] = None,
        device: str = "cuda",
    ):
        self.config_name = config_name
        self.checkpoint = Path(checkpoint) if checkpoint is not None else seg_cfg.CHECKPOINT_PATH
        self.device = device
        self._predictor = None
        self._load_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def load(self) -> None:
        with self._load_lock:
            if self._predictor is not None:
                return
            if not self.checkpoint.is_file():
                raise FileNotFoundError(
                    f"EfficientTAM checkpoint missing: {self.checkpoint} "
                    "(run scripts/download_efficienttam.sh)"
                )
            from efficient_track_anything.build_efficienttam import (
                build_efficienttam_camera_predictor,
            )

            install_connected_components_fallback()
            t0 = 0.0
            try:
                import time
                t0 = time.perf_counter()
                self._predictor = build_efficienttam_camera_predictor(
                    config_file=self.config_name,
                    ckpt_path=str(self.checkpoint),
                    device=self.device,
                    mode="eval",
                    apply_postprocessing=False,
                    hydra_overrides_extra=[
                        "++model.compile_image_encoder=%s" % ("true" if seg_cfg.COMPILE_IMAGE_ENCODER else "false")
                    ],
                )
                logger.info(
                    "EfficientTAM loaded from %s in %.1fs (config=%s)",
                    self.checkpoint.name, time.perf_counter() - t0, self.config_name,
                )
                self._warmup()
            except Exception:
                self._predictor = None
                raise

    def _warmup(self) -> None:
        """Run one dummy encode to warm CUDA kernels (first click is ~6s
        otherwise because the image encoder is compiled lazily on first use).
        Best effort only — failures never block model availability."""
        try:
            dummy = np.full((512, 512, 3), 127, dtype=np.uint8)
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                self._predictor.load_first_frame(dummy)
                self._predictor.reset_state()
        except Exception:
            logger.warning("model warmup failed (ignored)", exc_info=True)

    @property
    def is_loaded(self) -> bool:
        return self._predictor is not None

    def reset(self) -> None:
        if self._predictor is not None:
            with torch.inference_mode():
                self._predictor.reset_state()

    # -- interaction ---------------------------------------------------------
    def select(self, img_rgb: np.ndarray, x_px: float, y_px: float) -> np.ndarray:
        """First click: initialise conditioning on this frame and segment."""
        pred = self._require()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            pred.load_first_frame(img_rgb)
            _, _, masks = pred.add_new_points_or_box(
                frame_idx=0, obj_id=0, points=[[x_px, y_px]], labels=[1]
            )
        return _masks_to_bool(masks)

    def add_point(self, img_rgb: np.ndarray, x_px: float, y_px: float, label: int) -> np.ndarray:
        """Refinement click on the current frame (keeps previous points)."""
        pred = self._require()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            prepare_frame_for_interaction(pred, img_rgb)
            _, _, masks = pred.add_new_points_or_box(
                frame_idx=pred.frame_idx,
                obj_id=0,
                points=[[x_px, y_px]],
                labels=[int(label)],
                clear_old_points=False,
            )
            # Consolidate the new click / re-lock mask into the memory bank
            # so the NEXT track() step actually adopts it (the vendored
            # track() only preflights once, before the first frame).
            pred.propagate_in_video_preflight()
        return _masks_to_bool(masks)

    # -- streaming -----------------------------------------------------------
    def track(self, img_rgb: np.ndarray) -> np.ndarray:
        """Track the object on the next frame (memory-based)."""
        pred = self._require()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            _, masks = pred.track(img_rgb)
        return _masks_to_bool(masks)

    # -- internals ------------------------------------------------------------
    def _require(self):
        if self._predictor is None:
            self.load()
        return self._predictor


def _masks_to_bool(masks: torch.Tensor) -> np.ndarray:
    """(1,1,H,W) logits -> (H,W) bool numpy at video (fed-frame) resolution."""
    return (masks[0, 0] > 0.0).cpu().numpy()


def prepare_frame_for_interaction(predictor, img_rgb: np.ndarray) -> "torch.Tensor":
    """Make `predictor.frame_idx` addressable inside condition_state["images"].

    The vendored streaming fork only writes frame 0 into
    condition_state["images"] (load_first_frame); its track() never appends
    the current frame. So `add_new_points_or_box(frame_idx=N)` for N >= 1
    misses the feature cache in `_get_image_feature` and raises
    `IndexError: list index out of range` on `images[N]`. We pad the list
    with the current (normalised, model-res) frame tensor so a refinement /
    auto re-lock point runs on exactly the frame the user clicked.
    Intermediate slots are only ever appends and are never read.

    Returns the prepared tensor (kept for test assertions).
    """
    img_t, _w, _h = predictor.perpare_data(img_rgb, image_size=predictor.image_size)
    images = predictor.condition_state["images"]
    while len(images) <= predictor.frame_idx:
        images.append(img_t)
    return img_t