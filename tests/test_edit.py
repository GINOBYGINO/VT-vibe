"""Tests for module 4 edit."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from common.io import write_json
from common.job_store import JobStore
from common.schemas import (
    Highlight,
    HighlightsFile,
    Transcript,
    TranscriptSegment,
)
from modules.edit.runner import find_ffmpeg, run, slice_transcript


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_edit_produces_shorts(tmp_path: Path) -> None:
    store = JobStore.create(tmp_path, "https://www.youtube.com/watch?v=edit")
    paths = store.paths
    ffmpeg = find_ffmpeg()
    # 2s color video with silent audio
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1280x720:d=2",
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
            str(paths.raw_video),
        ],
        check=True,
        capture_output=True,
    )
    write_json(
        paths.highlights_json,
        HighlightsFile(
            highlights=[
                Highlight(
                    id=1,
                    start=0.0,
                    end=1.5,
                    title="測試",
                    reason="unit",
                    suggested_hook="hook",
                    score=1.0,
                    hour_bucket=0,
                )
            ]
        ),
    )
    write_json(
        paths.full_transcript_json,
        Transcript(
            language="zh",
            segments=[TranscriptSegment(id=0, start=0.2, end=1.0, text="你好")],
        ),
    )

    outputs = run(paths.root)
    assert len(outputs) == 1
    assert outputs[0].is_file()
    assert paths.short_transcript(1).is_file()


def test_slice_transcript_relative() -> None:
    full = Transcript(
        language="zh",
        segments=[
            TranscriptSegment(id=0, start=10.0, end=12.0, text="a"),
            TranscriptSegment(id=1, start=20.0, end=21.0, text="b"),
        ],
    )
    clipped = slice_transcript(full, 9.0, 13.0)
    assert len(clipped.segments) == 1
    assert clipped.segments[0].start == pytest.approx(1.0)
    assert clipped.segments[0].end == pytest.approx(3.0)
