"""Tests for module 5 subtitle burn-in."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from common.io import write_json
from common.job_store import JobStore
from common.schemas import Transcript, TranscriptSegment
from modules.subtitle.runner import escape_ass_filter_path, run


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def test_escape_ass_filter_path() -> None:
    escaped = escape_ass_filter_path(Path("C:/tmp/file.ass"))
    assert r"C\:" in escaped or "C:" not in escaped.replace(r"\:", "")


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_subtitle_burn_in(tmp_path: Path) -> None:
    store = JobStore.create(tmp_path, "https://www.youtube.com/watch?v=sub")
    paths = store.paths
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    nosub = paths.short_nosub(1)
    nosub.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(nosub),
        ],
        check=True,
        capture_output=True,
    )
    write_json(
        paths.short_transcript(1),
        Transcript(
            language="zh",
            segments=[TranscriptSegment(id=0, start=0.0, end=1.5, text="字幕測試")],
        ),
    )

    outputs = run(paths.root)
    assert len(outputs) == 1
    assert outputs[0].is_file()
    assert paths.short_ass(1).is_file()
