"""Device sync workflow for EasyTrans."""

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig
from easytrans.db import hash_exists
from easytrans.files import audio_path, compute_file_hash, next_file_id
from easytrans.models import Memo

# Audio file extensions we recognize from recorders
AUDIO_EXTENSIONS = {".mp3", ".wma", ".wav", ".ogg", ".flac", ".m4a", ".aac"}


def get_audio_duration(file_path: Path) -> float | None:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def mount_recorder(config: EasyTransConfig) -> None:
    """Mount the voice recorder device."""
    mount_point = Path(config.recorder.mount_point)
    mount_point.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sudo", "mount", config.recorder.device_path, str(mount_point)],
        check=True,
    )


def unmount_recorder(config: EasyTransConfig) -> None:
    """Unmount the voice recorder device."""
    subprocess.run(
        ["sudo", "umount", config.recorder.mount_point],
        check=True,
    )


def scan_recorder(config: EasyTransConfig) -> list[Path]:
    """Find all audio files on the mounted recorder."""
    recorder_dir = Path(config.recorder.mount_point) / config.recorder.audio_dir
    if not recorder_dir.exists():
        return []

    files = []
    for f in sorted(recorder_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(f)
    return files


def sync_files(
    config: EasyTransConfig,
    session: Session,
    recorder_files: list[Path],
) -> list[Memo]:
    """Copy new recordings from the recorder into the data directory.

    Returns a list of newly created Memo objects.
    """
    new_memos: list[Memo] = []

    for src_file in recorder_files:
        file_hash = compute_file_hash(src_file)
        if hash_exists(session, file_hash):
            continue

        # Get the recording timestamp from the file's modification time
        stat = src_file.stat()
        recorded_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        year = recorded_at.year

        file_id = next_file_id(config.audio_dir, year)
        ext = src_file.suffix  # preserve original extension

        dest = audio_path(config.data_dir, file_id, ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)

        duration = get_audio_duration(dest)

        memo = Memo(
            file_hash=file_hash,
            file_id=file_id,
            recorded_at=recorded_at,
            synced_at=datetime.now(tz=timezone.utc),
            duration_seconds=duration,
            completed=False,
        )
        session.add(memo)
        session.flush()
        new_memos.append(memo)

    return new_memos


def find_new_files(
    session: Session,
    recorder_files: list[Path],
) -> list[tuple[Path, str]]:
    """Check which recorder files haven't been synced yet.

    Returns list of (path, file_hash) tuples for new files.
    """
    new = []
    for f in recorder_files:
        file_hash = compute_file_hash(f)
        if not hash_exists(session, file_hash):
            new.append((f, file_hash))
    return new


def copy_single_file(
    config: EasyTransConfig,
    session: Session,
    src_file: Path,
    file_hash: str,
) -> Memo:
    """Copy a single recording from the recorder to the data directory.

    Returns the newly created Memo (flushed but not committed).
    """
    stat = src_file.stat()
    recorded_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    year = recorded_at.year

    file_id = next_file_id(config.audio_dir, year)
    ext = src_file.suffix

    dest = audio_path(config.data_dir, file_id, ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dest)

    duration = get_audio_duration(dest)

    memo = Memo(
        file_hash=file_hash,
        file_id=file_id,
        recorded_at=recorded_at,
        synced_at=datetime.now(tz=timezone.utc),
        duration_seconds=duration,
        completed=False,
    )
    session.add(memo)
    session.flush()
    return memo


def run_sync(config: EasyTransConfig, session: Session) -> list[Memo]:
    """Full sync workflow: mount, scan, copy, unmount.

    For testing, mount/unmount are commented out. The recorder
    directory is expected to already be accessible.
    """
    config.ensure_dirs()

    mount_recorder(config)
    try:
        recorder_files = scan_recorder(config)
        new_memos = sync_files(config, session, recorder_files)
    finally:
        unmount_recorder(config)

    return new_memos
