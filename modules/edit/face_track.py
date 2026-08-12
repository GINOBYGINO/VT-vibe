"""OpenCV face detection for face-gated digital zoom."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from common.layout import DEFAULT_ROI_CX, DEFAULT_ROI_CY
from common.logging_utils import setup_logger

_logger = setup_logger("modules.edit.face_track")


@dataclass
class FaceRoi:
    cx: float
    cy: float
    detected: bool
    samples: int = 0
    hits: int = 0


def _load_cascades():
    import cv2

    cascades = []
    base = Path(cv2.data.haarcascades)
    for name in (
        "haarcascade_frontalface_default.xml",
        "haarcascade_frontalface_alt2.xml",
        "haarcascade_profileface.xml",
    ):
        path = base / name
        if path.is_file():
            cascades.append(cv2.CascadeClassifier(str(path)))
    return cascades


def detect_faces_bgr(image_bgr) -> list[tuple[int, int, int, int]]:
    """Return list of (x, y, w, h) face boxes; empty if none / OpenCV missing."""
    try:
        import cv2
    except ImportError:
        _logger.warning("opencv-python not installed; face detect skipped")
        return []

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    boxes: list[tuple[int, int, int, int]] = []
    for cascade in _load_cascades():
        found = cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(48, 48),
        )
        for x, y, w, h in found:
            boxes.append((int(x), int(y), int(w), int(h)))
        if boxes:
            break
    return boxes


def _largest_box(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return max(boxes, key=lambda b: b[2] * b[3])


def extract_frame_bgr(
    video_path: Path,
    t_sec: float,
    *,
    ffmpeg: str,
) -> np.ndarray | None:
    """Grab one BGR frame at t_sec via ffmpeg pipe."""
    try:
        import cv2
    except ImportError:
        return None

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, t_sec):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        # ffmpeg missing in some CI/test environments
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    arr = np.frombuffer(proc.stdout, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def estimate_face_roi(
    video_path: Path,
    start: float,
    end: float,
    *,
    ffmpeg: str,
    sample_count: int = 5,
) -> FaceRoi:
    """
    Sample frames in [start,end]; if faces found, return median cx/cy in [0,1].
    Zoom should only be enabled when detected=True.
    """
    if end <= start:
        return FaceRoi(cx=DEFAULT_ROI_CX, cy=DEFAULT_ROI_CY, detected=False)

    span = end - start
    # Prefer mid/upper portion of the clip (VTuber face often upper-center)
    fracs = np.linspace(0.15, 0.85, num=max(1, sample_count))
    centers: list[tuple[float, float]] = []
    samples = 0
    for frac in fracs:
        t = start + span * float(frac)
        frame = extract_frame_bgr(video_path, t, ffmpeg=ffmpeg)
        if frame is None:
            continue
        samples += 1
        h, w = frame.shape[:2]
        boxes = detect_faces_bgr(frame)
        if not boxes:
            continue
        x, y, bw, bh = _largest_box(boxes)
        cx = (x + bw / 2.0) / max(1, w)
        cy = (y + bh / 2.0) / max(1, h)
        centers.append((cx, cy))

    if not centers:
        _logger.info(
            "face miss start=%.1f end=%.1f samples=%d", start, end, samples
        )
        return FaceRoi(
            cx=DEFAULT_ROI_CX,
            cy=DEFAULT_ROI_CY,
            detected=False,
            samples=samples,
            hits=0,
        )

    cxs = sorted(c[0] for c in centers)
    cys = sorted(c[1] for c in centers)
    mid = len(cxs) // 2
    cx = float(np.clip(cxs[mid], 0.15, 0.85))
    # Bias slightly up within face for crop (eyes/forehead)
    cy = float(np.clip(cys[mid] * 0.92, 0.12, 0.65))
    _logger.info(
        "face hit start=%.1f end=%.1f hits=%d/%d cx=%.3f cy=%.3f",
        start,
        end,
        len(centers),
        samples,
        cx,
        cy,
    )
    return FaceRoi(
        cx=cx,
        cy=cy,
        detected=True,
        samples=samples,
        hits=len(centers),
    )
