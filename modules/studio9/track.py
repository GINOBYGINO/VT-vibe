"""Sample face ROI and apply deadzone + exponential smoothing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from common.layout import DEFAULT_ROI_CX, DEFAULT_ROI_CY
from common.logging_utils import setup_logger
from modules.edit.face_track import detect_faces_bgr, extract_frame_bgr

_logger = setup_logger("modules.studio9.track")

DEFAULT_STEP_SEC = 8.0
DEFAULT_DEADZONE = 0.12
DEFAULT_SMOOTH = 0.35
FALLBACK_CX = 0.5
FALLBACK_CY = 0.38
MAX_SAMPLES = 4
MISS_ABORT = 2


@dataclass
class RoiSample:
    t: float
    cx: float
    cy: float
    hit: bool


def _largest_center(boxes: list[tuple[int, int, int, int]], w: int, h: int) -> tuple[float, float]:
    x, y, bw, bh = max(boxes, key=lambda b: b[2] * b[3])
    cx = (x + bw / 2.0) / max(1, w)
    cy = (y + bh / 2.0) / max(1, h)
    return float(cx), float(cy)


def sample_rois(
    video_path: Path,
    start: float,
    end: float,
    *,
    ffmpeg: str,
    step_sec: float = DEFAULT_STEP_SEC,
) -> list[RoiSample]:
    if end <= start:
        return [RoiSample(t=start, cx=FALLBACK_CX, cy=FALLBACK_CY, hit=False)]
    samples: list[RoiSample] = []
    t = float(start)
    misses = 0
    while t < end and len(samples) < MAX_SAMPLES:
        frame = extract_frame_bgr(video_path, t, ffmpeg=ffmpeg)
        if frame is None:
            samples.append(RoiSample(t=t, cx=FALLBACK_CX, cy=FALLBACK_CY, hit=False))
            misses += 1
        else:
            h, w = frame.shape[:2]
            boxes = detect_faces_bgr(frame)
            if boxes:
                cx, cy = _largest_center(boxes, w, h)
                samples.append(RoiSample(t=t, cx=cx, cy=cy, hit=True))
                misses = 0
            else:
                samples.append(RoiSample(t=t, cx=FALLBACK_CX, cy=FALLBACK_CY, hit=False))
                misses += 1
        if misses >= MISS_ABORT and not any(s.hit for s in samples):
            break
        t += max(0.15, float(step_sec))
    if not samples:
        samples.append(RoiSample(t=start, cx=FALLBACK_CX, cy=FALLBACK_CY, hit=False))
    hits = sum(1 for s in samples if s.hit)
    _logger.info(
        "roi samples=%d hits=%d start=%.1f end=%.1f",
        len(samples),
        hits,
        start,
        end,
    )
    return samples


def smooth_rois(
    samples: list[RoiSample],
    *,
    deadzone: float = DEFAULT_DEADZONE,
    smooth: float = DEFAULT_SMOOTH,
    fallback_cx: float = FALLBACK_CX,
    fallback_cy: float = FALLBACK_CY,
) -> list[tuple[float, float, float]]:
    """
    Return (t, cx, cy) after hold-last-hit, deadzone, and exponential smoothing.

    ``smooth`` is the catch-up factor in (0, 1]; higher follows faster.
    Deadzone is the L-inf radius in normalized [0,1] where the target does not move.
    """
    alpha = min(1.0, max(0.05, float(smooth)))
    dz = max(0.0, float(deadzone))
    out: list[tuple[float, float, float]] = []
    last_hit_cx = fallback_cx
    last_hit_cy = fallback_cy
    cur_cx = fallback_cx
    cur_cy = fallback_cy
    target_cx = fallback_cx
    target_cy = fallback_cy
    for s in samples:
        if s.hit:
            last_hit_cx = s.cx
            last_hit_cy = s.cy
        raw_cx = last_hit_cx if (s.hit or last_hit_cx != fallback_cx) else fallback_cx
        raw_cy = last_hit_cy if (s.hit or last_hit_cy != fallback_cy) else fallback_cy
        if s.hit:
            raw_cx, raw_cy = s.cx, s.cy
        else:
            raw_cx, raw_cy = last_hit_cx, last_hit_cy
        if abs(raw_cx - target_cx) > dz or abs(raw_cy - target_cy) > dz:
            target_cx, target_cy = raw_cx, raw_cy
        cur_cx = cur_cx + alpha * (target_cx - cur_cx)
        cur_cy = cur_cy + alpha * (target_cy - cur_cy)
        out.append((float(s.t), float(cur_cx), float(cur_cy)))
    if not out:
        out.append((0.0, fallback_cx, fallback_cy))
    return out


def mean_roi(smoothed: list[tuple[float, float, float]]) -> tuple[float, float]:
    if not smoothed:
        return DEFAULT_ROI_CX, DEFAULT_ROI_CY
    cx = sum(x[1] for x in smoothed) / len(smoothed)
    cy = sum(x[2] for x in smoothed) / len(smoothed)
    return float(cx), float(cy)
