"""Shared export helpers — copy finals into versioned project outputs/."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from common.constants import PIPELINE_VERSION
from common.io import project_root
from common.logging_utils import setup_logger

_logger = setup_logger("common.export")


def output_version_tag() -> str:
    """Resolve OUTPUT_VERSION or fall back to current PIPELINE_VERSION."""
    env = (os.environ.get("OUTPUT_VERSION") or "").strip()
    if env:
        return env
    return f"v{PIPELINE_VERSION}"


def _version_number(tag: str) -> float | None:
    """Parse 'v0.12' / '0.12' → 0.12; unknown → None."""
    text = (tag or "").strip().lower()
    if text.startswith("v"):
        text = text[1:]
    m = re.match(r"^(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def uses_alias_subdir(version_tag: str | None = None) -> bool:
    """From v0.10 onward, finals go under outputs/<version>/<alias>/."""
    tag = version_tag or output_version_tag()
    num = _version_number(tag)
    return num is not None and num >= 0.10


def default_export_dir() -> Path:
    return project_root() / "outputs" / output_version_tag()


def resolve_export_root(
    *,
    alias: str | None = None,
    export_dir: str | Path | None = None,
) -> Path:
    """
    Resolve export directory.

    - If export_dir is set: use it as-is (caller owns AB / custom layouts).
    - If version >= v0.10 and alias is set: outputs/<version>/<alias>/
    - Else: outputs/<version>/ (flat; legacy v0.9 style)
    """
    if export_dir is not None:
        return Path(export_dir)
    root = default_export_dir()
    if uses_alias_subdir() and alias:
        return root / str(alias).strip()
    return root


def export_final_clip(
    src: Path,
    *,
    alias: str | None,
    job_id: str,
    n: int,
    export_dir: str | Path | None = None,
    name_suffix: str = "final",
) -> Path:
    """
    Copy short final into unified outputs folder.
    Name: {alias}_short_{n}_{name_suffix}.mp4 (default suffix ``final``).

    For v0.10+ (when export_dir is None), files land under outputs/<ver>/<alias>/.
    """
    out_root = resolve_export_root(alias=alias, export_dir=export_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    stem = (alias or job_id or "clip").strip() or "clip"
    dest = out_root / f"{stem}_short_{n}_{name_suffix}.mp4"
    shutil.copy2(src, dest)
    _logger.info("export -> %s", dest)
    return dest
