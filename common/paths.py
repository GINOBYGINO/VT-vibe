"""Job directory path helpers."""

from __future__ import annotations

from pathlib import Path


class JobPaths:
    def __init__(self, job_dir: str | Path) -> None:
        self.root = Path(job_dir).resolve()

    @property
    def job_json(self) -> Path:
        return self.root / "job.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def download(self) -> Path:
        return self.root / "01_download"

    @property
    def asr(self) -> Path:
        return self.root / "02_asr"

    @property
    def highlights(self) -> Path:
        return self.root / "03_highlights"

    @property
    def edit(self) -> Path:
        return self.root / "04_edit"

    @property
    def subtitle(self) -> Path:
        return self.root / "05_subtitle"

    def ensure_layout(self) -> None:
        for path in (
            self.root,
            self.logs,
            self.download,
            self.asr,
            self.highlights,
            self.edit,
            self.subtitle,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # Module 1
    @property
    def raw_video(self) -> Path:
        return self.download / "raw_video.mp4"

    @property
    def audio_wav(self) -> Path:
        return self.download / "audio.wav"

    @property
    def chatlog(self) -> Path:
        return self.download / "chatlog.json"

    @property
    def metadata(self) -> Path:
        return self.download / "metadata.json"

    # Module 2
    @property
    def full_transcript_json(self) -> Path:
        return self.asr / "full_transcript.json"

    @property
    def full_transcript_srt(self) -> Path:
        return self.asr / "full_transcript.srt"

    @property
    def volume_peaks(self) -> Path:
        return self.asr / "volume_peaks.json"

    # Module 3
    @property
    def candidates(self) -> Path:
        return self.highlights / "candidates.json"

    @property
    def highlights_json(self) -> Path:
        return self.highlights / "highlights.json"

    def short_nosub(self, n: int) -> Path:
        return self.edit / f"short_{n}_nosub.mp4"

    def short_transcript(self, n: int) -> Path:
        return self.edit / f"short_{n}_transcript.json"

    def short_ass(self, n: int) -> Path:
        return self.subtitle / f"short_{n}.ass"

    def short_final(self, n: int) -> Path:
        return self.subtitle / f"short_{n}_final.mp4"
