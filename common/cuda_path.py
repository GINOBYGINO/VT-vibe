"""Ensure NVIDIA pip wheels' DLLs are discoverable on Windows."""

from __future__ import annotations

import os
import sys
from importlib import metadata
from pathlib import Path


def _candidate_bin_dirs() -> list[Path]:
    dirs: list[Path] = []
    # Prefer package distribution files (nvidia.__file__ may be None for namespace pkgs).
    for dist_name in (
        "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12",
        "nvidia-cuda-nvrtc-cu12",
        "nvidia-cuda-runtime-cu12",
    ):
        try:
            dist = metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            continue
        locate = getattr(dist, "locate_file", None)
        if locate is None:
            continue
        root = Path(str(locate("")))
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin", "nvidia/cuda_runtime/bin"):
            candidate = root / sub
            if candidate.is_dir():
                dirs.append(candidate)

    # Fallback: scan site-packages/nvidia/*/bin
    for entry in sys.path:
        nvidia_root = Path(entry) / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for child in nvidia_root.iterdir():
            bin_dir = child / "bin"
            if bin_dir.is_dir():
                dirs.append(bin_dir)
    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def ensure_cuda_dll_path() -> None:
    """Add nvidia-* package bin dirs to DLL search path (Windows)."""
    if sys.platform != "win32":
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep) if path else []
    for bin_dir in _candidate_bin_dirs():
        text = str(bin_dir)
        try:
            os.add_dll_directory(text)
        except (OSError, AttributeError):
            pass
        if text not in parts:
            parts.insert(0, text)
    os.environ["PATH"] = os.pathsep.join(parts)
