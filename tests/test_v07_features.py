"""v0.7: no Gemini, face-gated zoom, export folder, chat normalize."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from common.export import default_export_dir, export_final_clip
from common.schemas import JobConfig
from modules.download.chat import normalize_chat_item
from modules.edit.face_track import FaceRoi, estimate_face_roi
from modules.edit.runner import resolve_zoom_roi


def test_no_llm_and_face_gate_defaults() -> None:
    assert "use_llm" not in JobConfig.model_fields
    assert JobConfig().require_face_for_zoom is True


def test_export_final_clip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OUTPUT_VERSION", raising=False)
    src = tmp_path / "short_1_final.mp4"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "outs"
    dest = export_final_clip(
        src, alias="test2", job_id="jobx", n=1, export_dir=out_dir
    )
    assert dest == out_dir / "test2_short_1_final.mp4"
    assert dest.is_file()
    assert default_export_dir().name == "v0.13"


def test_normalize_chat_item_runs() -> None:
    msg = normalize_chat_item(
        {
            "time_in_seconds": 12.5,
            "author": {"name": "a"},
            "message": [{"text": "草"}, {"text": "www"}],
        }
    )
    assert msg is not None
    assert msg.t == 12.5
    assert msg.message == "草www"


def test_face_gated_zoom_requires_detection() -> None:
    enabled, z, _cx, _cy = resolve_zoom_roi(
        {"cx": 0.4, "cy": 0.3}, enable_zoom=True, zoom_factor=1.12
    )
    assert enabled is True
    assert z > 1.0
    # When face miss → caller passes enable_zoom=False
    disabled, z2, _, _ = resolve_zoom_roi(
        {}, enable_zoom=False, zoom_factor=1.12
    )
    assert disabled is False
    assert z2 >= 1.0


def test_estimate_face_roi_no_video(tmp_path: Path) -> None:
    missing = tmp_path / "none.mp4"
    roi = estimate_face_roi(missing, 0.0, 1.0, ffmpeg="ffmpeg", sample_count=2)
    assert isinstance(roi, FaceRoi)
    assert roi.detected is False
