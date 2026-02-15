"""File ID generation, hashing, and path helpers for EasyTrans."""

import hashlib
from pathlib import Path


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file's contents."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def next_file_id(audio_dir: Path, year: int) -> str:
    """Generate the next sequential file ID for a given year.

    Scans audio/{year}/ to find the highest existing number
    and returns the next one in YYYY-NNNN format.
    """
    year_dir = audio_dir / str(year)
    if not year_dir.exists():
        return f"{year}-0001"

    existing = []
    for f in year_dir.iterdir():
        if f.stem.startswith(f"{year}-") and f.suffix != ".wav":
            try:
                num = int(f.stem.split("-", 1)[1])
                existing.append(num)
            except (ValueError, IndexError):
                continue

    next_num = max(existing, default=0) + 1
    return f"{year}-{next_num:04d}"


def audio_path(data_dir: Path, file_id: str, ext: str) -> Path:
    """Get the path for an audio file given its ID and extension."""
    year = file_id.split("-")[0]
    return data_dir / "audio" / year / f"{file_id}{ext}"


def wav_path(data_dir: Path, file_id: str) -> Path:
    """Get the path for a WAV file given its ID."""
    return audio_path(data_dir, file_id, ".wav")


def text_path(data_dir: Path, file_id: str) -> Path:
    """Get the path for a text/markdown file given its ID."""
    year = file_id.split("-")[0]
    return data_dir / "text" / year / f"{file_id}.md"


def find_source_audio(data_dir: Path, file_id: str) -> Path | None:
    """Find the source (non-WAV) audio file for a memo."""
    year = file_id.split("-")[0]
    year_dir = data_dir / "audio" / year
    if not year_dir.exists():
        return None
    for f in year_dir.iterdir():
        if f.stem == file_id and f.suffix.lower() != ".wav":
            return f
    return None
