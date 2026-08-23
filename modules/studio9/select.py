"""Pick the highest-scoring highlight windows."""

from __future__ import annotations


def select_top_highlights(highlights: list, *, n: int) -> list:
    """Keep the highest-score windows (stable by start time on ties)."""
    if n <= 0:
        return list(highlights)
    ranked = sorted(
        highlights,
        key=lambda h: (
            -float(getattr(h, "score", 0.0) or 0.0),
            float(getattr(h, "start", 0.0) or 0.0),
        ),
    )
    return ranked[:n]
