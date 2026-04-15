"""Device sync workflow for EasyTrans."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig
from easytrans.db import hash_exists
from easytrans.files import compute_file_hash
from easytrans.importer import get_audio_duration, import_audio_as_memo
from easytrans.models import Memo, SourceFile

# Audio file extensions we recognize from recorders
AUDIO_EXTENSIONS = {".mp3", ".wma", ".wav", ".ogg", ".flac", ".m4a", ".aac"}


def mount_recorder(config: EasyTransConfig) -> None:
    """Mount the voice recorder device."""
    mount_point = Path(config.recorder.mount_point)
    mount_point.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["sudo", "-n", "mount", config.recorder.device_path, str(mount_point)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"mount failed (exit {result.returncode}): {stderr}"
        )


def unmount_recorder(config: EasyTransConfig) -> None:
    """Unmount the voice recorder device."""
    result = subprocess.run(
        ["sudo", "-n", "umount", config.recorder.mount_point],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"umount failed (exit {result.returncode}): {stderr}"
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
    """
    Copy new recordings from the recorder into the data directory.

    Returns a list of newly created Memo objects. Unlike
    `copy_single_file`, this helper does not write SourceFile cache
    rows — it hashes every file on every call. Prefer the
    `find_new_files` + `copy_single_file` path in production code.
    """
    new_memos: list[Memo] = []

    for src_file in recorder_files:
        file_hash = compute_file_hash(src_file)
        if hash_exists(session, file_hash):
            continue

        recorded_at = datetime.fromtimestamp(
            src_file.stat().st_mtime, tz=timezone.utc,
        )
        memo = import_audio_as_memo(
            config, session, src_file, file_hash, recorded_at,
        )
        new_memos.append(memo)

    return new_memos


def _file_stat_key(file_path: Path) -> tuple[str, int, int]:
    """Extract (filename, size, mtime_ns) from a file — no content read needed."""
    stat = file_path.stat()
    return (file_path.name, stat.st_size, stat.st_mtime_ns)


def find_new_files(
    session: Session,
    recorder_files: list[Path],
) -> list[tuple[Path, str]]:
    """Check which recorder files haven't been synced yet.

    Uses the source_files table to avoid re-reading file contents
    from the recorder (expensive over USB). Files whose (filename,
    size, mtime_ns) match a cached entry get their hash from the DB
    instead of being re-read.

    Returns list of (path, file_hash) tuples for new files.
    """
    # Load all cached source file metadata in one query
    cached_rows = session.execute(
        select(SourceFile.filename, SourceFile.file_size,
               SourceFile.mtime_ns, SourceFile.file_hash)
    ).all()
    cache: dict[tuple[str, int, int], str] = {
        (row.filename, row.file_size, row.mtime_ns): row.file_hash
        for row in cached_rows
    }

    known_hashes: set[str] = set(session.scalars(select(Memo.file_hash)).all())

    new = []
    for f in recorder_files:
        key = _file_stat_key(f)
        cached_hash = cache.get(key)
        if cached_hash is not None:
            file_hash = cached_hash
        else:
            file_hash = compute_file_hash(f)
        if file_hash not in known_hashes:
            new.append((f, file_hash))

    return new


def copy_single_file(
    config: EasyTransConfig,
    session: Session,
    src_file: Path,
    file_hash: str,
) -> Memo:
    """
    Copy a single recording from the recorder into the data directory.

    Thin wrapper around `import_audio_as_memo` that adds the
    SourceFile dedup row — the metadata cache that lets future syncs
    skip re-hashing this file off the USB. Returns the newly created
    Memo (flushed but not committed).
    """
    stat = src_file.stat()
    recorded_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    memo = import_audio_as_memo(
        config, session, src_file, file_hash, recorded_at,
    )

    session.add(SourceFile(
        filename=src_file.name,
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        file_hash=file_hash,
    ))
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
