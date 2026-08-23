"""Prefer the project .venv so `python -m studio` is not system Python 3.14."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from common.io import project_root

_REQUIRED = ("yt_dlp", "fastapi")


def venv_python() -> Path | None:
    root = project_root()
    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def missing_modules(names: tuple[str, ...] = _REQUIRED) -> list[str]:
    missing: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            missing.append(name)
    return missing


def reexec_in_venv_if_needed() -> None:
    """If yt-dlp/fastapi missing, re-launch with `.venv` Python."""
    if os.environ.get("STUDIO_SKIP_VENV_REEXEC", "").strip() in {"1", "true", "yes"}:
        return
    missing = missing_modules()
    if not missing:
        return
    vpy = venv_python()
    current = Path(sys.executable).resolve()
    hint = " ".join(missing)
    if vpy is None:
        raise SystemExit(
            f"缺少套件 ({hint})。請先啟動專案虛擬環境後再執行：\n"
            f"  .\\.venv\\Scripts\\Activate.ps1\n"
            f"  python -m studio"
        )
    if current == vpy.resolve():
        raise SystemExit(
            f"目前已是 .venv，但仍缺少套件 ({hint})。請執行：\n"
            f"  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        )
    os.execv(str(vpy), [str(vpy), "-m", "studio"])
