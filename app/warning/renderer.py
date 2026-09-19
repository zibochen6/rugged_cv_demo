"""Industrial HMI renderer (§24-§33).

Single window, 1280x720 canvas:
  - live video is the visual主体 (full width, letterboxed into the area above
    the bottom bar)
  - danger ROI drawn as a semi-transparent polygon, emphasized by risk level
  - nearest obstacle: bounding box + distance label ("2.3 m")
  - bottom status bar: NEAREST | TTC | STATUS  (+ small camera/AI health dots)
  - SAFE clean / WARNING orange emphasis / DANGER red edge pulse (moderate,
    video stays visible) / SYSTEM ERROR distinct magenta/red state
  - D key cycles debug views INSIDE the same main area; debug panel is one
    compact translucent block (no extra windows)
All glyphs are shapes (cv2.putText has no unicode arrows/etc).
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

from app.warning.risk_engine import SAFE, WARNING, DANGER, SYSTEM_ERROR

# canvas
CANVAS_W, CANVAS_H = 1280, 720
BAR_H = 108
HEADER_H = 34
_DEPTH_INSET_W, _DEPTH_INSET_H = 320, 180

# palette (BGR), follows app/ui/product_ui.py industrial style
C_BG = (22, 24, 26)
C_CHROME = (35, 38, 32)
C_TEXT = (235, 235, 235)
C_DIM = (150, 150, 150)
C_GREEN = (90, 210, 110)
C_ORANGE = (45, 165, 255)
C_RED = (70, 80, 255)
C_MAGENTA = (255, 60, 200)
C_YELLOW = (60, 220, 255)
C_WHITE = (245, 245, 245)


class Renderer:
    def __init__(self) -> None:
        self.canvas = np.full((CANVAS_H, CANVAS_W, 3), 255, dtype=np.uint8)
        self.debug_view = 1  # 1..6
        self.fullscreen = False
        self.most_recent: dict = {}

    # ---------------------------------------------------------------- frame
    def _video_area(self):
        """(x, y, w, h) region for the live picture (16:9, letterboxed)."""
        h = CANVAS_H - BAR_H - HEADER_H
        w = CANVAS_W
        # fit 16:9 into the band
        if w * (9.0 / 16.0) > h:
            dw = int(h * 16.0 / 9.0)
            return ((w - dw) // 2, HEADER_H, dw, h)
        return (0, HEADER_H, w, int(w * 9.0 / 16.0))

    @staticmethod
    def _resize_fit(f: np.ndarray, w: int, h: int) -> np.ndarray:
        fh, fw = f.shape[:2]
        s = min(w / fw, h / fh)
        return cv2.resize(f, (max(1, int(fw * s)), max(1, int(fh * s))))

    def _draw_roi(self, norm_pts, x, y, w, h, level: str):
        if not norm_pts:
            return
        pts = (np.asarray(norm_pts, np.float32) *
               np.array([w, h], np.float32)).astype(np.int32)
        pts[:, 0] += x
        pts[:, 1] += y
        if level == SAFE:
            edge, fill_a = (120, 160, 120), 20
        elif level == WARNING:
            edge, fill_a = C_ORANGE, 45
        elif level == DANGER:
            edge, fill_a = C_RED, 60
        else:
            edge, fill_a = C_MAGENTA, 40
        overlay = self.canvas.copy()
        cv2.fillPoly(overlay, [pts], fill_a)
        self.canvas = cv2.addWeighted(overlay, 0.55, self.canvas, 0.45, 0)
        cv2.polylines(self.canvas, [pts], True, edge, 2, cv2.LINE_AA)

    def _draw_obstacle(self, bbox, dist_m, x, y, w, h):
        bx, by, bw, bh = bbox
        # bbox in camera coords -> video-area coords (aspect-agnostic)
        sx = w / (self.most_recent.get("cam_w") or w)
        sy = h / (self.most_recent.get("cam_h") or h)
        p1 = (x + int(bx * sx), y + int(by * sy))
        p2 = (x + int((bx + bw) * sx), y + int((by + bh) * sy))
        cv2.rectangle(self.canvas, p1, p2, C_YELLOW, 2, cv2.LINE_AA)
        label = (f"{dist_m:.1f} m"
                 if dist_m is not None and np.isfinite(dist_m)
                 else "DIST ?")
        ly = p1[1] - 10
        if ly < HEADER_H + 20:
            ly = p2[1] + 24
        cv2.rectangle(self.canvas, (p1[0], ly - 20),
                      (p1[0] + 110, ly + 4), C_BG, -1)
        cv2.putText(self.canvas, label, (p1[0] + 6, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, C_YELLOW, 2, cv2.LINE_AA)

    def _draw_people(self, people, x, y, w, h):
        sx = w / (self.most_recent.get("cam_w") or w)
        sy = h / (self.most_recent.get("cam_h") or h)
        for person in people or ():
            bx, by, bw, bh = person.bbox
            p1 = (x + int(bx * sx), y + int(by * sy))
            p2 = (x + int((bx + bw) * sx), y + int((by + bh) * sy))
            color = (
                C_RED
                if person.distance_valid
                and person.distance_m <= self.most_recent.get("danger_m", 1.5)
                else C_ORANGE
            )
            cv2.rectangle(self.canvas, p1, p2, color, 3, cv2.LINE_AA)
            distance = (f"{person.distance_m:.1f}m"
                        if person.distance_valid else "DIST ?")
            label = f"PERSON #{person.track_id} {distance}"
            cv2.rectangle(self.canvas, (p1[0], max(HEADER_H, p1[1] - 25)),
                          (min(x + w, p1[0] + 220), p1[1]), C_BG, -1)
            cv2.putText(self.canvas, label,
                        (p1[0] + 4, max(HEADER_H + 17, p1[1] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    def _draw_depth_inset(self, depth_map, x, y, w=_DEPTH_INSET_W,
                          h=_DEPTH_INSET_H):
        """Small depth-heatmap PiP in the top-left of the picture."""
        if depth_map is None:
            return
        img = colorize_depth(depth_map)
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
        self.canvas[y:y + h, x:x + w] = img
        cv2.rectangle(self.canvas, (x, y), (x + w, y + h), C_DIM, 1, cv2.LINE_AA)
        cv2.rectangle(self.canvas, (x, y), (x + 118, y + 18), C_BG, -1)
        cv2.putText(self.canvas, "DEPTH", (x + 5, y + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, C_YELLOW, 1, cv2.LINE_AA)

    def _draw_collision_view(self, d_filt, danger_m, warn_m, level, x, y,
                             w=110, h=150):
        """Vertical collision gauge: ego dot (bottom) + obstacle 'wall' bar vs zones."""
        gmax = 5.0  # vertical scale, meters (bottom=0m, top=5m)
        try:
            danger_m = max(0.05, float(danger_m))
            warn_m = max(0.05, float(warn_m))
        except (TypeError, ValueError):
            danger_m, warn_m = 1.5, 3.0

        # opaque box bg + border
        cv2.rectangle(self.canvas, (x, y), (x + w, y + h), C_BG, -1)
        cv2.rectangle(self.canvas, (x, y), (x + w, y + h), C_DIM, 1, cv2.LINE_AA)

        y_top, y_bot = y + 22, y + h - 16
        cx = x + w // 2
        gx0, gx1 = x + 8, x + w - 8

        def ym(m):
            m = max(0.0, min(float(m), gmax))
            return int(y_bot - (m / gmax) * (y_bot - y_top))

        # three zones (bottom=0m red, mid orange, top green)
        y_d = ym(danger_m)
        y_w = ym(warn_m)
        cv2.rectangle(self.canvas, (gx0, y_d), (gx1, y_bot), C_RED, -1)
        cv2.rectangle(self.canvas, (gx0, y_w), (gx1, y_d), C_ORANGE, -1)
        cv2.rectangle(self.canvas, (gx0, y_top), (gx1, y_w), C_GREEN, -1)

        # ego dot (bottom)
        cv2.circle(self.canvas, (cx, y_bot - 4), 4, C_WHITE, -1)

        # obstacle 'wall' bar at current distance
        col = self._state_color(level) if d_filt == d_filt else C_DIM
        ywl = ym(d_filt) if d_filt == d_filt else y_top
        cv2.rectangle(self.canvas, (gx0, ywl - 3), (gx1, ywl + 3), col, -1)
        cv2.rectangle(self.canvas, (gx0, ywl - 3), (gx1, ywl + 3), C_WHITE, 1)

        # labels
        cv2.putText(self.canvas, "DIST", (x + 6, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, C_DIM, 1, cv2.LINE_AA)
        txt = f"{d_filt:.2f} m" if d_filt == d_filt else "-- m"
        cv2.putText(self.canvas, txt, (x + 34, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        cv2.putText(self.canvas, "0", (x + 3, y + h - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, C_DIM, 1, cv2.LINE_AA)
        cv2.putText(self.canvas, f"{gmax:.0f}m", (x + w - 24, y + 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, C_WHITE, 1, cv2.LINE_AA)

    @staticmethod
    def _state_color(level: str):
        return {SAFE: C_GREEN, WARNING: C_ORANGE,
                DANGER: C_RED, SYSTEM_ERROR: C_MAGENTA}[level]

    def _draw_header(self, level, fps, cam_ok, ai_ok):
        self.canvas[:HEADER_H, :] = C_BG
        cv2.putText(self.canvas, f"{fps:4.1f} FPS", (CANVAS_W - 150, 23),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_DIM, 1, cv2.LINE_AA)
        # health dots: camera / AI
        for k, ok, dx in (("CAM", cam_ok, 0), ("AI", ai_ok, 90)):
            col = C_GREEN if ok else C_RED
            cv2.circle(self.canvas, (14 + dx, 17), 6, col, -1)
            cv2.putText(self.canvas, k, (28 + dx, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, C_TEXT, 1, cv2.LINE_AA)
        st_col = self._state_color(level)
        cv2.rectangle(self.canvas, (CANVAS_W - 500, 0),
                      (CANVAS_W, HEADER_H), st_col, -1)
        cv2.putText(self.canvas, level, (CANVAS_W - 482, 23),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, C_BG, 2, cv2.LINE_AA)

    def _draw_bar(self, level, d_filt, ttc, velocity):
        y0 = CANVAS_H - BAR_H
        cols = {SAFE: C_CHROME, WARNING: (52, 68, 105),
                DANGER: (45, 30, 70), SYSTEM_ERROR: (60, 25, 60)}[level]
        self.canvas[y0:, :] = cols
        w3 = CANVAS_W // 3
        cv2.line(self.canvas, (w3, y0 + 12), (w3, CANVAS_H - 12), C_DIM, 1)
        cv2.line(self.canvas, (w3 * 2, y0 + 12), (w3 * 2, CANVAS_H - 12),
                 C_DIM, 1)
        def cell(x0, big, small):
            cv2.putText(self.canvas, small, (x0 + 24, y0 + 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_DIM, 1, cv2.LINE_AA)
            cv2.putText(self.canvas, big, (x0 + 24, y0 + 78),
                        cv2.FONT_HERSHEY_DUPLEX, 1.1, C_TEXT, 2, cv2.LINE_AA)
        cell(0, f"{d_filt:.1f} m" if d_filt == d_filt else "--",
             "NEAREST")
        ttc_txt = f"{ttc:.1f} s" if (ttc is not None and ttc == ttc) else "--"
        cell(w3, ttc_txt, "TTC")
        vel_txt = f"{velocity:+.1f} m/s" if velocity is not None and \
            velocity == velocity else ""
        cell(w3 * 2, self._status_big(level), "STATUS" + vel_txt)

    @staticmethod
    def _status_big(level: str) -> str:
        return {SAFE: "SAFE", WARNING: "SLOW", DANGER: "STOP",
                SYSTEM_ERROR: "ERROR"}[level]

    def _draw_edge_pulse(self, level: str):
        if level not in (DANGER, SYSTEM_ERROR):
            return
        t = time.time()
        k = float(level == DANGER)  # 1 damping pulse / 0.5 for error
        a = int(90 + 70 * math.sin(t * (2.4 if k else 1.6)))
        col = C_RED if level == DANGER else C_MAGENTA
        border = max(4, CANVAS_H // 42)
        self.canvas[:border, :] = col
        self.canvas[-border:, :] = col
        self.canvas[:, :border] = col
        self.canvas[:, -border:] = col
        # blend so video remains visible
        overlay = self.canvas.copy()
        cv2.rectangle(overlay, (0, 0), (CANVAS_W - 1, CANVAS_H - 1),
                      tuple(int(c) for c in col), border)
        self.canvas = cv2.addWeighted(overlay, min(a, 255) / 255.0,
                                      self.canvas, 1 - min(a, 255) / 255.0, 0)

    # ------------------------------------------------------------- assemble
    def render(self, video_frame, level, d_filt, ttc_s, velocity,
               norm_roi, obstacle, fps, cam_ok, ai_ok, debug: dict | None = None,
               **aux) -> np.ndarray:
        """Return the composed 1280x720 canvas.

        aux keys: depth_map (HxW), ground_mask, obstacle_mask,
                  roi_mask, cam_w, cam_h, expected (HxW float),
                  frame_id (V0.4: capture fidx — watermarked on canvas so
                  the web <img> frame can be matched to /state fidx)
        """
        self.most_recent = {"cam_w": video_frame.shape[1],
                            "cam_h": video_frame.shape[0],
                            "danger_m": aux.get("person_danger_m", 1.5),
                            "frame_id": aux.get("frame_id", None)}
        x, y, w, h = self._video_area()
        self.canvas[:] = C_BG

        # main visual per debug view
        view = self.debug_view
        if view == 2 and aux.get("depth_map") is not None:
            canvas_img = colorize_depth(aux["depth_map"])
        elif view == 3 and aux.get("depth_map") is not None:
            canvas_img = colorize_depth(aux["depth_map"])
        elif view == 4 and aux.get("ground_mask") is not None:
            canvas_img = mask_tint(video_frame, aux["ground_mask"], C_GREEN)
        elif view == 5 and aux.get("obstacle_mask") is not None:
            canvas_img = mask_tint(video_frame, aux["obstacle_mask"], C_RED)
        elif view == 6 and aux.get("roi_mask") is not None:
            canvas_img = mask_tint(video_frame, aux["roi_mask"], C_ORANGE)
        else:
            canvas_img = video_frame
        pic = self._resize_fit(canvas_img, w, h)
        px = x + (w - pic.shape[1]) // 2
        py = y + (h - pic.shape[0]) // 2
        self.canvas[py:py + pic.shape[0], px:px + pic.shape[1]] = pic

        # overlay (video-space coordinates derived from the picture block)
        px, py, pw, ph = px, py, pic.shape[1], pic.shape[0]
        self._draw_roi(norm_roi, px, py, pw, ph, level)
        self._draw_people(aux.get("people"), px, py, pw, ph)
        # obstacle bbox omitted (per request); depth-map inset shows instead
        if view == 1 and aux.get("depth_map") is not None:
            self._draw_depth_inset(aux["depth_map"], px + 8,
                                   py + 8)

        if view == 1:
            self._draw_collision_view(d_filt, aux.get("danger_m", 1.5),
                                      aux.get("warn_m", 3.0), level,
                                      px + 8, py + ph - 150 - 8)
        self._draw_header(level, fps, cam_ok, ai_ok)
        self._draw_frame_watermark(aux.get("frame_id", None))
        self._draw_bar(level, d_filt, ttc_s, velocity)
        if debug is not None:
            self._draw_debug_panel(debug)
        self._draw_edge_pulse(level)
        return self.canvas

    def _draw_frame_watermark(self, frame_id):
        """V0.4: burn the capture frame id on every canvas so the web <img>
        can be matched against the live /state fidx (LAG audit)."""
        if frame_id is None:
            return
        tag = f"F#{int(frame_id)}"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        x, y = CANVAS_W - tw - 14, CANVAS_H - BAR_H - 10
        cv2.rectangle(self.canvas, (x - 6, y - th - 6),
                      (x + tw + 6, y + 6), C_BG, -1)
        cv2.putText(self.canvas, tag, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, C_DIM, 1, cv2.LINE_AA)

    def _draw_debug_panel(self, dbg: dict):
        lines = []
        for k, v in dbg.items():
            if isinstance(v, float):
                lines.append(f"{k}: {v:.3f}")
            else:
                lines.append(f"{k}: {v}")
        n = len(lines)
        if not n:
            return
        bw, bh = 360, 20 + n * 22
        bx, by = CANVAS_W - bw - 12, HEADER_H + 12
        overlay = self.canvas.copy()
        cv2.rectangle(overlay, (bx, by), (bx + bw, by + bh), (0, 0, 0), -1)
        self.canvas = cv2.addWeighted(overlay, 0.72, self.canvas, 0.28, 0)
        for i, ln in enumerate(lines):
            cv2.putText(self.canvas, ln, (bx + 12, by + 26 + i * 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, C_TEXT, 1, cv2.LINE_AA)


# ----------------------------------------------------------------- color utils
_DEPTH_COLORS = None


def colorize_depth(depth_m: np.ndarray, vmin: float = 0.3,
                   vmax: float = 8.0) -> np.ndarray:
    """Inferno colormap visualization of a float depth map (invalid -> dark)."""
    d = np.asarray(depth_m, dtype=np.float32)
    valid = np.isfinite(d)
    v = np.zeros_like(d)
    v[valid] = np.clip((d[valid] - vmin) / max(1e-6, vmax - vmin), 0.0, 1.0)
    idx = (v * 255.0 + 0.5).astype(np.uint8)
    return cv2.applyColorMap(idx, cv2.COLORMAP_INFERNO)


def mask_tint(frame: np.ndarray, mask: np.ndarray, color) -> np.ndarray:
    img = frame.copy()
    m = (np.asarray(mask) > 0).astype(np.uint8)
    if m.shape[:2] != img.shape[:2]:
        m = cv2.resize(m, (img.shape[1], img.shape[0]))
    overlay = np.zeros_like(img)
    overlay[m > 0] = color
    return cv2.addWeighted(img, 0.6, overlay, 0.4, 0)
