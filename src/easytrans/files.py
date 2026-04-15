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
    """
    Generate the next sequential file ID for a given year.

    Scans audio/{year}/ to find the highest existing number and returns
    the next one in YYYY-NNNN format. Identity is by *stem*, not full
    filename, so a memo with both a source file and a derivative WAV
    (e.g. `2026-0001.mp3` and `2026-0001.wav`) occupies a single slot,
    and a memo whose source is itself a WAV (direct in-app recording)
    also correctly occupies its slot.
    """
    year_dir = audio_dir / str(year)
    if not year_dir.exists():
        return f"{year}-0001"

    used: set[int] = set()
    for f in year_dir.iterdir():
        if not f.stem.startswith(f"{year}-"):
            continue
        try:
            used.add(int(f.stem.split("-", 1)[1]))
        except (ValueError, IndexError):
            continue

    return f"{year}-{(max(used, default=0) + 1):04d}"


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
    """
    Find the source audio file for a memo.

    Prefers a non-WAV sibling (the original compressed source for synced
    memos), but falls back to the WAV itself when it's the only file for
    the stem (the case for in-app direct recordings, where the WAV *is*
    the source).
    """
    year = file_id.split("-")[0]
    year_dir = data_dir / "audio" / year
    if not year_dir.exists():
        return None
    wav_fallback: Path | None = None
    for f in year_dir.iterdir():
        if f.stem != file_id:
            continue
        if f.suffix.lower() == ".wav":
            wav_fallback = f
        else:
            return f
    return wav_fallback
