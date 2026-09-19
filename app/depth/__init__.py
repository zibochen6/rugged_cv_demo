"""3D click-to-track depth package (Phase 10A+).

Depth is ONLY responsible for "how far is each pixel". Object discovery and
masking belong to EfficientTAM; geometry belongs to app/geometry.
"""
from app.depth.estimator import MetricDepthEstimator

__all__ = ["MetricDepthEstimator"]
