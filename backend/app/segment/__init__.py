"""Click-to-segment (EfficientTAM) minimal feature.

Pipeline:
  user click on the live preview -> single-object segmentation via
  EfficientTAM camera predictor -> per-frame tracking with memory ->
  mask polygons streamed to the web overlay.

Scope (deliberately minimal):
  - single target, left-click positive points, optional negative points
  - lost -> ghost (last good mask) retained; reappear -> auto resume
  - no multi-object, no YOLO, no saving, no video files
"""