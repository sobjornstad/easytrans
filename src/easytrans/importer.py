"""
Shared primitive for turning an audio file on disk into a Memo.

Every path that brings an audio file into EasyTrans — syncing from a
USB voice recorder, recording directly into the app, and any future
source (phone, shared folder, etc.) — ends up calling
`import_audio_as_memo`. Keeping the file-placement, hashing, and row
insertion in one place ensures memos look identical regardless of
origin.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig
from easytrans.files import audio_path, next_file_id
from easytrans.models import Memo


def get_audio_duration(file_path: Path) -> float | None:
    "Return the duration of an audio file in seconds, or None if ffprobe fails."
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


def import_audio_as_memo(
    config: EasyTransConfig,
    session: Session,
    src_file: Path,
    file_hash: str,
    recorded_at: datetime,
    move: bool = False,
) -> Memo:
    """
    Place `src_file` under the data directory and create a Memo row.

    Assigns the next sequential file ID for the year implied by
    `recorded_at`, copies (or moves, if `move=True`) the file into
    `audio/{year}/{file_id}{ext}`, measures its duration, and inserts
    a Memo. The memo is flushed but not committed — callers are
    responsible for the surrounding transaction.

    No `SourceFile` dedup row is written here; that side table is
    specific to the USB-recorder sync path and is managed by
    `sync.copy_single_file`.
    """
    year = recorded_at.year
    file_id = next_file_id(config.audio_dir, year)
    ext = src_file.suffix

    dest = audio_path(config.data_dir, file_id, ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src_file), dest)
    else:
        shutil.copy2(src_file, dest)

    memo = Memo(
        file_hash=file_hash,
        file_id=file_id,
        recorded_at=recorded_at,
        synced_at=datetime.now(tz=timezone.utc),
        duration_seconds=get_audio_duration(dest),
        completed=False,
    )
    session.add(memo)
    session.flush()
    return memo
