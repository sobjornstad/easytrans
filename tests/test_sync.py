"""Tests for sync workflow."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig, RecorderConfig, WhisperConfig
from easytrans.db import hash_exists
from easytrans.files import compute_file_hash
from easytrans.models import Base, Memo
from easytrans.sync import scan_recorder, sync_files


def _make_config(tmp_path: Path, recorder_dir: Path) -> EasyTransConfig:
    return EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(
            device_path="/dev/null",
            mount_point=str(recorder_dir.parent.parent),
            audio_dir=str(recorder_dir.relative_to(recorder_dir.parent.parent)),
        ),
        whisper=WhisperConfig(),
    )


def _setup_recorder(tmp_path: Path) -> Path:
    """Create a fake recorder directory with some files."""
    recorder_dir = tmp_path / "mount" / "RECORDER" / "FOLDER_B"
    recorder_dir.mkdir(parents=True)
    (recorder_dir / "memo1.mp3").write_bytes(b"audio data 1")
    (recorder_dir / "memo2.mp3").write_bytes(b"audio data 2")
    (recorder_dir / "notes.txt").write_bytes(b"not audio")  # should be skipped
    return recorder_dir


def test_scan_recorder(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    files = scan_recorder(config)
    assert len(files) == 2
    assert all(f.suffix == ".mp3" for f in files)


def test_scan_recorder_empty(tmp_path: Path) -> None:
    recorder_dir = tmp_path / "mount" / "RECORDER" / "FOLDER_B"
    recorder_dir.mkdir(parents=True)
    config = _make_config(tmp_path, recorder_dir)
    files = scan_recorder(config)
    assert len(files) == 0


def test_scan_recorder_missing_dir(tmp_path: Path) -> None:
    config = EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(
            mount_point=str(tmp_path / "nonexistent"),
            audio_dir="FOLDER",
        ),
    )
    files = scan_recorder(config)
    assert len(files) == 0


def test_sync_files_creates_memos(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        new_memos = sync_files(config, session, recorder_files)
        session.commit()

        assert len(new_memos) == 2
        assert new_memos[0].file_id.startswith("2026-")
        # Files should be copied to audio dir
        for memo in new_memos:
            year = memo.file_id.split("-")[0]
            copied = config.audio_dir / year / f"{memo.file_id}.mp3"
            assert copied.exists()


def test_sync_files_skips_duplicates(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        # First sync
        first = sync_files(config, session, recorder_files)
        session.commit()
        assert len(first) == 2

        # Second sync - should skip all
        second = sync_files(config, session, recorder_files)
        session.commit()
        assert len(second) == 0


def test_sync_sequential_ids(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        memos = sync_files(config, session, recorder_files)
        session.commit()

        ids = sorted(m.file_id for m in memos)
        # Should be sequential
        assert ids[0].endswith("-0001")
        assert ids[1].endswith("-0002")
