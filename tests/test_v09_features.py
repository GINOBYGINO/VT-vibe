"""v0.9: Cursor-first gate + cookiesfrombrowser chat opts."""

from __future__ import annotations

from pathlib import Path

from common.schemas import ChatMessage
from common.ytdlp_util import (
    base_ytdlp_opts,
    browser_cookie_candidates,
    cookies_from_browser,
)
from modules.download.chat import _cookie_hint, normalize_chat_item
from modules.highlights.runner import write_cursor_review_prompt, write_decisions_example
from modules.highlights.scoring import peak_seed_times


def _should_wait(*, review_wait: bool, has_decisions: bool, auto_arcs: bool) -> bool:
    """Mirror pipeline.py Cursor gate."""
    return review_wait or (not has_decisions and not auto_arcs)


def test_default_cursor_wait_gate() -> None:
    assert _should_wait(review_wait=False, has_decisions=False, auto_arcs=False)
    assert not _should_wait(review_wait=False, has_decisions=True, auto_arcs=False)
    assert not _should_wait(review_wait=False, has_decisions=False, auto_arcs=True)
    assert _should_wait(review_wait=True, has_decisions=True, auto_arcs=True)


def test_cookies_from_browser_opts(monkeypatch, tmp_path, chdir=None) -> None:
    monkeypatch.delenv("YTDLP_COOKIES", raising=False)
    monkeypatch.delenv("YOUTUBE_COOKIES", raising=False)
    monkeypatch.setenv("YTDLP_BROWSER", "edge")
    monkeypatch.chdir(tmp_path)
    assert browser_cookie_candidates() == ["edge"]
    assert cookies_from_browser() == ("edge",)
    opts = base_ytdlp_opts(quiet=True)
    assert "cookiefile" not in opts
    assert opts.get("cookiesfrombrowser") == ("edge",)


def test_cookie_file_wins_over_browser(monkeypatch, tmp_path) -> None:
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape\n", encoding="utf-8")
    monkeypatch.setenv("YTDLP_COOKIES", str(cookie))
    monkeypatch.setenv("YTDLP_BROWSER", "chrome")
    opts = base_ytdlp_opts(quiet=True)
    assert opts.get("cookiefile") == str(cookie)
    assert "cookiesfrombrowser" not in opts


def test_cursor_review_prompt_quality_instructions(tmp_path: Path) -> None:
    path = tmp_path / "cursor_review_prompt.md"
    decisions = tmp_path / "review_decisions.json"
    write_cursor_review_prompt(
        path,
        content_type="talk",
        candidates=[
            {
                "candidate_id": 1,
                "start": 10,
                "end": 80,
                "suggested_start": 12,
                "suggested_end": 70,
                "score": 3.2,
                "title": "demo",
                "suggested_hook": "hook",
                "speech_ratio": 0.8,
                "chat_density": 0.1,
                "mean_zscore": 1.0,
                "keyword_hits": 0,
                "emotion_score": 0.5,
                "transcript_excerpt": "哈哈哈好扯",
                "breakdown": {"chat": 0.1},
                "chat_weak": True,
            }
        ],
        decisions_path=decisions,
        chat_weak=True,
        clips_per_hour=4,
    )
    text = path.read_text(encoding="utf-8")
    assert "好笑" in text or "有梗" in text
    assert "chat_weak" in text
    assert "reject" in text
    assert "每小時" in text
    write_decisions_example(tmp_path / "review_decisions.example.json", [{"candidate_id": 1}])
    assert (tmp_path / "review_decisions.example.json").is_file()


def test_chat_cookie_hint_mentions_browser() -> None:
    hint = _cookie_hint(had_cookie_file=False, last_reason="parse")
    assert "YTDLP_COOKIES" in hint or "YTDLP_BROWSER" in hint
    assert "Cursor" in hint or "prefer_Cursor" in hint


def test_normalize_replay_chat_offset_on_action() -> None:
    item = {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": "12345",
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "message": {"runs": [{"text": "哈哈哈"}]},
                                "authorName": {"simpleText": "viewer"},
                            }
                        }
                    }
                }
            ],
        }
    }
    msg = normalize_chat_item(item)
    assert msg is not None
    assert abs(msg.t - 12.345) < 1e-6
    assert msg.message == "哈哈哈"
    assert msg.author == "viewer"


def test_chat_burst_seed_more_sensitive() -> None:
    messages = [ChatMessage(t=float(i), author="u", message="x") for i in range(0, 20, 1)]
    # dense 5s bin around 0-5
    for i in range(6):
        messages.append(ChatMessage(t=1.0 + i * 0.2, author="u", message="burst"))
    seeds_loose = peak_seed_times(
        [], [], messages, duration=100.0, chat_burst_mult=1.2
    )
    seeds_strict = peak_seed_times(
        [], [], messages, duration=100.0, chat_burst_mult=3.0
    )
    assert len(seeds_loose) >= len(seeds_strict)
