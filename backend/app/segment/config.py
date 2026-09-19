"""Defaults for the click-to-segment feature."""
import os
from pathlib import Path

# --- model ---------------------------------------------------------------
# Hydra config name, resolved by hydra relative to whichever
# efficient_track_anything package model.py selected (see THIRD_PARTY_DIR).
MODEL_CONFIG = "configs/efficienttam/efficienttam_ti_512x512.yaml"
# Official checkpoint — already present on the device at
# <repo>/checkpoints/efficienttam_ti_512x512.pt (no download needed).
CHECKPOINT_NAME = "efficienttam_ti_512x512.pt"
# Compile is disabled: JetPack torch 2.1 has no triton on aarch64.
COMPILE_IMAGE_ENCODER = False

# --- frame sizes ---------------------------------------------------------
# Camera frames are downscaled (max side) before the model; polygons are
# reported at the web overlay resolution for direct canvas drawing.
FRAME_MAX_SIDE = 640
OVERLAY_W = 1280
OVERLAY_H = 720
TARGET_FPS = 10.0             # latest-frame inference cap; rear warning stays priority

# --- loss / reappear hysteresis ------------------------------------------
# area ratio = mask pixels / frame pixels
LOST_AREA_RATIO = 0.001      # below this the object is considered absent
LOST_FRAMES = 6              # consecutive absent frames before LOST
RESUME_AREA_RATIO = 0.002    # mask must come back above this…
RESUME_FRAMES = 2            # …for this many consecutive frames to resume

# --- interaction ---------------------------------------------------------
MAX_POINTS = 16              # refinement point cap (then clear to re-pick)
MIN_POLYGON_AREA = 24.0      # overlay-res px; smaller contours are dropped
POLYGON_EPSILON = 1.0        # approxPolyDP epsilon (model-res px)

# --- mask quality gate ---------------------------------------------------
# A single positive point on a hand-held object makes EfficientTAM segment
# the whole connected foreground (hand + object) — that is not a model bug
# but SAM behaviour. We flag such over-segmentation so the UI can ask the
# user for a right-click negative point before any auto-resume is armed.
MASK_QUALITY_POOR_AREA_RATIO = 0.30  # mask area ratio above this -> poor
MASK_QUALITY_POOR_BBOX_RATIO = 0.60  # mask bbox covering this much of a frame side -> poor

# --- identity template ---------------------------------------------------
TEMPLATE_ERODE_PX = 3   # erode the model-res mask before building/using the
                        # appearance template: removes the target/context
                        # blend contour so the template is the object core

# --- lost re-acquisition (verified template re-lock) ---------------------
# EfficientTAM's memory window (7 frames) forgets the object's appearance
# during long occlusions; while LOST we periodically template-match the
# last good appearance against the current frame and auto re-click at the
# peak, so a reappearing object resumes tracking without user interaction.
# The candidate mask must additionally pass *identity verification* against
# the appearance template before it is committed, otherwise a different
# object occupying the old spot would be re-locked instead.
RESEED_INTERVAL_S = 1.5      # template search cadence while lost
RESEED_THRESHOLD = 0.65      # TM_CCOEFF_NORMED min score to consider a peak
RESEED_MIN_PSR = 4.0         # peak significance (PSR vs response-map sidelobes)
RESEED_CONFIRM_TRIES = 2     # consecutive confirmed attempts at one peak
RESEED_POS_TOL = 0.03        # normalized-position tolerance (confirm/suppress)
RESEED_SUPPRESS_S = 5.0      # cool-down (s) for rejected/foreign peaks
ID_MATCH_THRESHOLD = 0.6     # identity verification min correlation
TEMPLATE_REFRESH_CORR = 0.8  # min identity corr to adopt a fresh exemplar into the anchor
NATURAL_RESUME_MATCH_THRESHOLD = 0.5  # min identity corr for model-natural resume (more tolerant than reseed)
TEMPLATE_SIZE = 48           # template side (px), aspect preserved

# --- paths ---------------------------------------------------------------
SEGMENT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = SEGMENT_DIR.parent.parent.parent  # backend/app/segment -> repo root
# Vendored EfficientTAM package (tracked in git). model.py prepends this
# directory to sys.path, so the segment service runs THIS copy; the venv
# egg-link to <repo>/third_party/EfficientTAM stays as the fallback for a
# plain `import efficient_track_anything` outside the wrapper.
THIRD_PARTY_DIR = SEGMENT_DIR / "third_party"
CHECKPOINT_DIR = REPO_ROOT / "checkpoints"


def resolve_checkpoint() -> Path:
    """Use the single repository-root checkpoint copy."""
    cand = CHECKPOINT_DIR / CHECKPOINT_NAME
    if cand.is_file():
        return cand
    return CHECKPOINT_DIR / CHECKPOINT_NAME


CHECKPOINT_PATH = resolve_checkpoint()
