"""Camera intrinsics calibration — checkerboard (Phase 13.10).

Produces configs/camera_calibration.yaml (fx, fy, cx, cy, k1..k3, p1, p2)
together with the RMS reprojection error (§43: parameters alone are not
enough). The forklift demo merges it via --camera-config.

Two modes:
  live capture:
    python scripts/calibrate_camera_intrinsics.py --source usb:0 \
        --board 9x6 --square-mm 25
    SPACE = grab frame when the board is fully visible (needs >=8 views with
    varied pose) · s = calibrate+save · q = abort
  offline (testable):
    python scripts/calibrate_camera_intrinsics.py --images "dir/*.png" ...

Pure cv2 — no new dependencies.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import yaml

OUT = "configs/camera_calibration.yaml"


def board_object_points(inner: tuple[int, int], square_m: float) -> np.ndarray:
    return np.array([[(x * square_m, y * square_m, 0.0)
                      for x in range(inner[0])] for y in range(inner[1])],
                    dtype=np.float32).reshape(-1, 3)


def calibrate_from_images(paths, inner=(9, 6), square_mm: float = 25.0,
                          min_views: int = 5):
    """(paths, board) -> dict with K/distortion/rms or raises RuntimeError."""
    objp = board_object_points(inner, square_mm / 1000.0)
    obj_points, img_points, size = [], [], None
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if size is None:
            size = (gray.shape[1], gray.shape[0])
        elif (gray.shape[1], gray.shape[0]) != size:
            print(f"[intr-calib] skip {p}: size mismatch")
            continue
        ok, corners = cv2.findChessboardCorners(gray, inner, None)
        if not ok:
            print(f"[intr-calib] skip {p}: board not found")
            continue
        corners = cv2.cornerSubPix(
            gray, corners, (11, 11), (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))
        obj_points.append(objp)
        img_points.append(corners)
        print(f"[intr-calib] view {len(obj_points)}: {os.path.basename(p)}")
    if len(obj_points) < min_views:
        raise RuntimeError(
            f"only {len(obj_points)} valid views (<{min_views}); move the "
            "board around and capture more")
    rms, K, dist, _rvecs, _tvecs = cv2.calibrateCamera(
        obj_points, img_points, size, None, None,
        flags=cv2.CALIB_FIX_K3 if len(obj_points) < 10 else 0)
    return {
        "rms": float(rms), "K": K, "dist": dist, "size": size,
        "n_views": len(obj_points),
        "board": {"inner": list(inner), "square_mm": float(square_mm)},
    }


def write_yaml(path: str, res: dict) -> None:
    K, dist = res["K"], res["dist"].ravel()
    w, h = res["size"]
    data = {
        "camera": {
            "width": int(w), "height": int(h),
            "calibrated": True,
            "intrinsics": {
                "fx": round(float(K[0, 0]), 4), "fy": round(float(K[1, 1]), 4),
                "cx": round(float(K[0, 2]), 4), "cy": round(float(K[1, 2]), 4),
            },
            "distortion": {
                "k1": float(dist[0]), "k2": float(dist[1]),
                "p1": float(dist[2]), "p2": float(dist[3]),
                "k3": float(dist[4]) if len(dist) > 4 else 0.0,
            },
            "calibration": {
                "method": "checkerboard cv2.calibrateCamera",
                "rms_reprojection_error_px": round(res["rms"], 4),
                "n_views": res["n_views"],
                "board_inner_corners": res["board"]["inner"],
                "board_squares": res["board"].get(
                    "squares",
                    [res["board"]["inner"][0] + 1,
                     res["board"]["inner"][1] + 1]),
                "board_square_mm": res["board"]["square_mm"],
                "timestamp": time.time(),
            },
        }
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    print(f"[intr-calib] wrote {path}  (RMS = {res['rms']:.3f} px, "
          f"{res['n_views']} views)")
    print(f"  fx={K[0, 0]:.2f} fy={K[1, 1]:.2f} cx={K[0, 2]:.2f} "
          f"cy={K[1, 2]:.2f}")


def live_capture(args) -> int:
    from app.camera_source import open_source
    inner = tuple(int(v) for v in args.board.split("x"))
    cap, label, _pace = open_source(args.source)
    assert cap.isOpened()
    objp = board_object_points(inner, args.square_mm / 1000.0)
    obj_points, img_points, size = [], [], None
    win = "intrinsics calibration"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    print("[intr-calib] SPACE=grab, s=save+calibrate, q=abort "
          f"(board {inner}, {args.square_mm} mm squares)")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        size = (gray.shape[1], gray.shape[0])
        found, corners = cv2.findChessboardCorners(gray, inner, None)
        disp = frame.copy()
        if found:
            cv2.drawChessboardCorners(disp, inner, corners, True)
        cv2.putText(disp, f"views: {len(obj_points)}"
                    + ("  BOARD FOUND - SPACE to grab" if found
                       else "  show the full board"),
                    (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if found else (80, 80, 255), 2)
        cv2.imshow(win, disp)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            return 1
        if key == ord(" ") and found:
            corners = cv2.cornerSubPix(
                gray, corners, (11, 11), (-1, -1),
                (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))
            obj_points.append(objp)
            img_points.append(corners)
        if key == ord("s"):
            break
    cap.release()
    cv2.destroyAllWindows()
    if len(obj_points) < 5:
        print(f"[intr-calib] need >=5 views, got {len(obj_points)}")
        return 1
    rms, K, dist, _r, _t = cv2.calibrateCamera(
        obj_points, img_points, size, None, None)
    write_yaml(args.out, {"rms": float(rms), "K": K, "dist": dist,
                          "size": size, "n_views": len(obj_points),
                          "board": {"inner": list(inner),
                                    "square_mm": args.square_mm}})
    return 0


def offline(args) -> int:
    inner = tuple(int(v) for v in args.board.split("x"))
    paths = sorted(glob.glob(args.images))
    if not paths:
        print(f"[intr-calib] no images match {args.images}")
        return 1
    try:
        res = calibrate_from_images(paths, inner, args.square_mm)
    except RuntimeError as e:
        print(f"[intr-calib] FAILED: {e}")
        return 1
    write_yaml(args.out, res)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="live capture, e.g. usb:0")
    ap.add_argument("--images", default=None, help='offline glob, e.g. "calib/*.png"')
    ap.add_argument("--board", default="9x6", help="inner corners WxH")
    ap.add_argument("--square-mm", type=float, default=25.0)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    if args.images:
        sys.exit(offline(args))
    if args.source:
        sys.exit(live_capture(args))
    print("need --source or --images")
    sys.exit(2)


if __name__ == "__main__":
    main()
