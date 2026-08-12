"""Ensure hook SFX assets exist (vendor or synthesize)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from common.io import project_root
from common.logging_utils import setup_logger

_logger = setup_logger("modules.hook.sfx")

SFX_NAMES = (
    "tape_windup.wav",
    "keyboard_click.wav",
    "whoosh.wav",
    "tv_noise.wav",
)

SR = 44100


def sfx_dir() -> Path:
    return project_root() / "assets" / "sfx"


def _write_wav(path: Path, audio: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mono = np.asarray(audio, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 1e-6:
        mono = mono / peak * 0.85
    sf.write(str(path), mono, sr)


def synth_tape_windup(sr: int = SR, dur: float = 1.5) -> np.ndarray:
    """Rising noise chirp (tape / fast-forward feel)."""
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    noise = np.random.default_rng(42).normal(0, 1, n).astype(np.float32)
    # Band-pass-ish via multiply with rising sine carrier
    f0, f1 = 400.0, 4500.0
    phase = 2 * np.pi * (f0 * t + 0.5 * (f1 - f0) / dur * t * t)
    carrier = np.sin(phase).astype(np.float32)
    env = np.linspace(0.35, 1.0, n).astype(np.float32)
    return noise * 0.35 * env + carrier * 0.25 * env


def synth_keyboard_click(sr: int = SR) -> np.ndarray:
    """Short click impulse."""
    n = int(sr * 0.045)
    t = np.arange(n) / sr
    click = np.exp(-t * 180.0) * np.sin(2 * np.pi * 1800 * t)
    click += 0.4 * np.exp(-t * 90.0) * np.random.default_rng(7).normal(0, 1, n)
    return click.astype(np.float32)


def synth_whoosh(sr: int = SR, dur: float = 0.45) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    noise = np.random.default_rng(99).normal(0, 1, n).astype(np.float32)
    env = np.sin(np.pi * t / dur).astype(np.float32) ** 1.5
    # Simple one-pole lowpass sweep via cumulative blend
    out = np.zeros(n, dtype=np.float32)
    state = 0.0
    for i in range(n):
        cutoff = 0.05 + 0.45 * (i / max(1, n - 1))
        state = state + cutoff * (float(noise[i]) - state)
        out[i] = state * env[i]
    return out


def synth_tv_noise(sr: int = SR, dur: float = 0.35) -> np.ndarray:
    n = int(sr * dur)
    noise = np.random.default_rng(123).normal(0, 1, n).astype(np.float32)
    env = np.ones(n, dtype=np.float32)
    fade = int(0.04 * sr)
    if fade > 0 and n > 2 * fade:
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
    # Sparse crackle
    crackle = (np.abs(noise) > 2.2).astype(np.float32) * noise
    return (noise * 0.35 + crackle * 0.5) * env


def ensure_sfx(*, force_synth: bool = False) -> dict[str, Path]:
    """
    Return mapping of logical name → wav path.
    Synthesize any missing files into assets/sfx/.
    """
    root = sfx_dir()
    root.mkdir(parents=True, exist_ok=True)
    attribution = root / "ATTRIBUTION.md"
    if not attribution.is_file():
        attribution.write_text(
            "# SFX attribution\n\n"
            "Default assets are **synthesized** by `modules.hook.sfx` "
            "(no third-party sample required).\n"
            "Replace files in this folder with CC0 / cleared WAVs if desired; "
            "`ensure_sfx()` will use existing files as-is.\n",
            encoding="utf-8",
        )

    makers = {
        "tape_windup.wav": synth_tape_windup,
        "keyboard_click.wav": synth_keyboard_click,
        "whoosh.wav": synth_whoosh,
        "tv_noise.wav": synth_tv_noise,
    }
    out: dict[str, Path] = {}
    for name, maker in makers.items():
        path = root / name
        if force_synth or not path.is_file() or path.stat().st_size < 64:
            _logger.info("synthesizing SFX %s", name)
            _write_wav(path, maker())
        out[name] = path
    return out
