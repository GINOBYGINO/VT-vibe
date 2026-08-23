"""Kinetic emphasis on karaoke lines using existing flourish keyword lists."""

from __future__ import annotations

from modules.flourish.runner import MAX_PER_10S, MAX_PER_SENTENCE, _is_content_word, _load_keywords

__all__ = [
    "MAX_PER_10S",
    "MAX_PER_SENTENCE",
    "_is_content_word",
    "emphasis_words",
]


def emphasis_words(stream_type: str, texts: list[str]) -> list[str]:
    """Pick up to MAX_PER_SENTENCE content keywords appearing in texts."""
    kws = _load_keywords(stream_type if stream_type in {"talk", "game"} else "talk")
    found: list[str] = []
    blob = "".join(texts)
    for k in sorted((x for x in kws if _is_content_word(x)), key=len, reverse=True):
        if k in blob and k not in found:
            found.append(k)
        if len(found) >= MAX_PER_SENTENCE:
            break
    return found
