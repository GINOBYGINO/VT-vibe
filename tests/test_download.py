"""Tests for module 1 download helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from common.io import read_model, write_json
from common.schemas import ChatLog, ChatMessage, Metadata
from modules.download.runner import fetch_chatlog, info_to_metadata


def test_metadata_roundtrip(tmp_path: Path) -> None:
    meta = Metadata(
        id="abc",
        title="測試標題",
        channel="頻道",
        duration_sec=3661.5,
        url="https://www.youtube.com/watch?v=abc",
    )
    path = tmp_path / "metadata.json"
    write_json(path, meta)
    loaded = read_model(path, Metadata)
    assert loaded.id == "abc"
    assert loaded.duration_sec == 3661.5


def test_chatlog_schema_roundtrip(tmp_path: Path) -> None:
    chat = ChatLog(
        available=True,
        messages=[ChatMessage(t=12.5, author="a", message="草")],
    )
    path = tmp_path / "chatlog.json"
    write_json(path, chat)
    loaded = read_model(path, ChatLog)
    assert loaded.available is True
    assert loaded.messages[0].message == "草"


def test_fetch_chatlog_failure_degrades() -> None:
    with patch("modules.download.runner.ChatDownloader") as cls:
        cls.return_value.get_chat.side_effect = RuntimeError("boom")
        result = fetch_chatlog("https://www.youtube.com/watch?v=x", retries=1)
    assert result.available is False
    assert result.messages == []
    assert result.error_reason is not None


def test_info_to_metadata() -> None:
    info = {
        "id": "d6wJVaDzNBE",
        "title": "t",
        "channel": "c",
        "duration": 7200,
    }
    meta = info_to_metadata(info, "https://www.youtube.com/watch?v=d6wJVaDzNBE")
    assert meta.id == "d6wJVaDzNBE"
    assert meta.duration_sec == 7200.0
