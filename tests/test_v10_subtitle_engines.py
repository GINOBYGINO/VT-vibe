"""v0.10: fast ASR for selection + WhisperX for subtitles + alias export folders."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from common.export import default_export_dir, export_final_clip, resolve_export_root
from modules.asr.runner import (
    _env_use_whisperx_for_subtitle,
    use_whisperx_for_module2,
)
from modules.subtitle.runner import (
    _subtitle_engines_for_alias,
    subtitle_ab_test5,
    use_whisperx_for_subtitle,
)


def test_module2_skips_whisperx_when_subtitle_only(monkeypatch) -> None:
    monkeypatch.setenv("USE_WHISPERX", "1")
    monkeypatch.setenv("USE_WHISPERX_FOR_SUBTITLE", "1")
    assert _env_use_whisperx_for_subtitle() is True
    assert use_whisperx_for_module2() is False


def test_module2_uses_whisperx_when_full_flag_only(monkeypatch) -> None:
    monkeypatch.delenv("USE_WHISPERX_FOR_SUBTITLE", raising=False)
    monkeypatch.setenv("USE_WHISPERX", "1")
    assert use_whisperx_for_module2() is True


def test_subtitle_engines_ab_test5(monkeypatch) -> None:
    monkeypatch.setenv("SUBTITLE_AB_TEST5", "1")
    monkeypatch.setenv("USE_WHISPERX_FOR_SUBTITLE", "1")
    assert subtitle_ab_test5() is True
    assert _subtitle_engines_for_alias("test5") == ["fast", "whisperx"]
    # Non-test5 with whisperx-for-subtitle → only whisperx
    assert _subtitle_engines_for_alias("test2") == ["whisperx"]


def test_subtitle_engines_legacy_when_flags_off(monkeypatch) -> None:
    monkeypatch.delenv("SUBTITLE_AB_TEST5", raising=False)
    monkeypatch.delenv("USE_WHISPERX_FOR_SUBTITLE", raising=False)
    assert use_whisperx_for_subtitle() is False
    assert _subtitle_engines_for_alias("test5") == []


def test_export_v010_alias_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_VERSION", "v0.10")
    # resolve_export_root uses project_root(); patch default_export_dir via env
    # and pass export_dir=None so alias nesting applies.
    with patch("common.export.default_export_dir", return_value=tmp_path / "v0.10"):
        root = resolve_export_root(alias="test2", export_dir=None)
        assert root == tmp_path / "v0.10" / "test2"

        src = tmp_path / "short_1_final.mp4"
        src.write_bytes(b"fake")
        dest = export_final_clip(
            src, alias="test2", job_id="jobx", n=1, export_dir=None
        )
        assert dest == tmp_path / "v0.10" / "test2" / "test2_short_1_final.mp4"
        assert dest.is_file()


def test_export_v012_alias_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_VERSION", "v0.12")
    with patch("common.export.default_export_dir", return_value=tmp_path / "v0.12"):
        root = resolve_export_root(alias="test6", export_dir=None)
        assert root == tmp_path / "v0.12" / "test6"

        src = tmp_path / "short_2_final.mp4"
        src.write_bytes(b"fake")
        dest = export_final_clip(
            src, alias="test6", job_id="jobx", n=2, export_dir=None
        )
        assert dest == tmp_path / "v0.12" / "test6" / "test6_short_2_final.mp4"
        assert dest.is_file()


def test_export_explicit_dir_unchanged(tmp_path: Path) -> None:
    src = tmp_path / "short_1_final.mp4"
    src.write_bytes(b"fake")
    out_dir = tmp_path / "outs"
    dest = export_final_clip(
        src, alias="test2", job_id="jobx", n=1, export_dir=out_dir
    )
    assert dest == out_dir / "test2_short_1_final.mp4"
    assert dest.is_file()


def test_export_ab_variant_dir(tmp_path: Path) -> None:
    src = tmp_path / "short_1_final.mp4"
    src.write_bytes(b"fake")
    variant = tmp_path / "v0.10" / "test5" / "fast"
    dest = export_final_clip(
        src, alias="test5", job_id="jobx", n=1, export_dir=variant
    )
    assert dest == variant / "test5_short_1_final.mp4"
    assert dest.is_file()
