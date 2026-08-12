"""Shared yt-dlp / cookies helpers for download + chat."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def cookies_path() -> str | None:
    env = (os.environ.get("YTDLP_COOKIES") or os.environ.get("YOUTUBE_COOKIES") or "").strip()
    if env and Path(env).is_file():
        return env
    for candidate in (Path("cookies.txt"), Path("configs") / "cookies.txt"):
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def browser_cookie_candidates() -> list[str]:
    """Browsers to try for cookiesfrombrowser (YTDLP_BROWSER overrides)."""
    env = (os.environ.get("YTDLP_BROWSER") or "").strip().lower()
    if env:
        return [env]
    return ["chrome", "edge"]


def cookies_from_browser() -> tuple[str, ...] | None:
    """Primary browser tuple for yt-dlp `cookiesfrombrowser`."""
    browsers = browser_cookie_candidates()
    if not browsers:
        return None
    return (browsers[0],)


def js_runtimes() -> dict[str, dict]:
    """Prefer node, then deno — reduces YouTube parse failures."""
    if shutil.which("node"):
        return {"node": {}}
    if shutil.which("deno"):
        return {"deno": {}}
    # Still declare node so yt-dlp surfaces a clear warning if missing
    return {"node": {}}


def apply_cookie_opts(opts: dict[str, Any], *, prefer_browser: str | None = None) -> dict[str, Any]:
    """
    Cookie file (YTDLP_COOKIES) wins; else cookiesfrombrowser.
    prefer_browser: force a specific browser name when using browser cookies.
    """
    cookie = cookies_path()
    if cookie:
        opts["cookiefile"] = cookie
        opts.pop("cookiesfrombrowser", None)
        return opts
    browser = prefer_browser or (browser_cookie_candidates()[0] if browser_cookie_candidates() else None)
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
        opts.pop("cookiefile", None)
    return opts


def base_ytdlp_opts(
    *,
    quiet: bool = False,
    prefer_browser: str | None = None,
    use_cookies: bool = True,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "js_runtimes": js_runtimes(),
        # YouTube n-challenge solver (yt-dlp EJS); needed with recent player
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtube": {"player_client": ["android", "web"]},
        },
        "quiet": quiet,
        "noprogress": quiet,
        "no_warnings": quiet,
    }
    if use_cookies:
        return apply_cookie_opts(opts, prefer_browser=prefer_browser)
    return opts
