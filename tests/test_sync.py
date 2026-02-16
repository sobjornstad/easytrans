"""Tests for sync workflow."""

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig, RecorderConfig, WhisperConfig
from easytrans.db import hash_exists
from easytrans.files import compute_file_hash
from easytrans.models import Base, Memo, SourceFile
from easytrans.sync import (
    copy_single_file,
    find_new_files,
    mount_recorder,
    scan_recorder,
    sync_files,
    unmount_recorder,
)


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


# --- Tests for find_new_files and copy_single_file ---


def test_find_new_files_returns_all_when_empty_db(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        new = find_new_files(session, recorder_files)
        assert len(new) == 2
        # Each entry is (path, hash)
        for path, file_hash in new:
            assert path.suffix == ".mp3"
            assert len(file_hash) == 64  # SHA-256 hex


def test_find_new_files_skips_already_synced(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        # Sync all files first
        sync_files(config, session, recorder_files)
        session.commit()

        # Now find_new_files should return nothing
        new = find_new_files(session, recorder_files)
        assert len(new) == 0


def test_find_new_files_returns_only_new(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        # Sync only the first file
        first_hash = compute_file_hash(recorder_files[0])
        copy_single_file(config, session, recorder_files[0], first_hash)
        session.commit()

        # find_new_files should return only the second
        new = find_new_files(session, recorder_files)
        assert len(new) == 1
        assert new[0][0] == recorder_files[1]


def test_copy_single_file_creates_memo(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        src = recorder_dir / "memo1.mp3"
        file_hash = compute_file_hash(src)
        memo = copy_single_file(config, session, src, file_hash)
        session.commit()

        assert memo.file_hash == file_hash
        assert memo.file_id.endswith("-0001")
        assert memo.completed is False
        assert memo.recorded_at is not None
        assert memo.synced_at is not None

        # File should be copied
        year = memo.file_id.split("-")[0]
        copied = config.audio_dir / year / f"{memo.file_id}.mp3"
        assert copied.exists()

        # Should be in the database
        assert hash_exists(session, file_hash)


def test_copy_single_file_sequential_ids(tmp_path: Path) -> None:
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        new_files = find_new_files(session, recorder_files)

        memos = []
        for src, file_hash in new_files:
            memo = copy_single_file(config, session, src, file_hash)
            memos.append(memo)
        session.commit()

        ids = sorted(m.file_id for m in memos)
        assert ids[0].endswith("-0001")
        assert ids[1].endswith("-0002")


# --- Tests for mount/unmount ---


def _mount_config(tmp_path: Path) -> EasyTransConfig:
    return EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(
            device_path="/dev/sda1",
            mount_point=str(tmp_path / "mount"),
        ),
    )


@patch("easytrans.sync.subprocess.run")
def test_mount_recorder_passes_correct_args(mock_run, tmp_path: Path) -> None:
    """Regression: shell=True with list args caused only 'sudo' to run."""
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    config = _mount_config(tmp_path)

    mount_recorder(config)

    args = mock_run.call_args
    cmd = args[0][0]  # first positional arg
    assert cmd == ["sudo", "-n", "mount", "/dev/sda1", str(tmp_path / "mount")]
    assert args[1].get("shell") is not True  # shell=True must not be used with list


@patch("easytrans.sync.subprocess.run")
def test_unmount_recorder_passes_correct_args(mock_run, tmp_path: Path) -> None:
    """Regression: shell=True with list args caused only 'sudo' to run."""
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    config = _mount_config(tmp_path)

    unmount_recorder(config)

    args = mock_run.call_args
    cmd = args[0][0]
    assert cmd == ["sudo", "-n", "umount", str(tmp_path / "mount")]
    assert args[1].get("shell") is not True


@patch("easytrans.sync.subprocess.run")
def test_mount_recorder_raises_on_failure(mock_run, tmp_path: Path) -> None:
    """mount_recorder must raise with stderr details when mount fails."""
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="mount: permission denied\n",
    )
    config = _mount_config(tmp_path)

    with pytest.raises(RuntimeError, match="mount failed.*permission denied"):
        mount_recorder(config)


@patch("easytrans.sync.subprocess.run")
def test_unmount_recorder_raises_on_failure(mock_run, tmp_path: Path) -> None:
    """unmount_recorder must raise with stderr details when umount fails."""
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="umount: not mounted\n",
    )
    config = _mount_config(tmp_path)

    with pytest.raises(RuntimeError, match="umount failed.*not mounted"):
        unmount_recorder(config)


@patch("easytrans.sync.subprocess.run")
def test_mount_creates_mount_point(mock_run, tmp_path: Path) -> None:
    """mount_recorder should create the mount point directory if it doesn't exist."""
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    config = _mount_config(tmp_path)
    mount_dir = tmp_path / "mount"
    assert not mount_dir.exists()

    mount_recorder(config)

    assert mount_dir.exists()


# --- Tests for source_files cache in find_new_files ---


def test_find_new_files_uses_cache_on_second_call(tmp_path: Path) -> None:
    """After copy_single_file stores source metadata, find_new_files should
    skip re-reading those files from the recorder."""
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        # Copy files (populates both memos and source_files tables)
        new_files = find_new_files(session, recorder_files)
        for src, fhash in new_files:
            copy_single_file(config, session, src, fhash)
        session.commit()

        # Verify source_files were stored
        count = session.query(SourceFile).count()
        assert count == 2

        # Second call: should use cached hashes (not re-read files)
        with patch("easytrans.sync.compute_file_hash") as mock_hash:
            new = find_new_files(session, recorder_files)
            assert len(new) == 0
            mock_hash.assert_not_called()


def test_find_new_files_hashes_new_file_not_in_cache(tmp_path: Path) -> None:
    """Files not in source_files must be hashed by reading them."""
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        recorder_files = scan_recorder(config)
        # Copy existing files (populates source_files cache)
        new_files = find_new_files(session, recorder_files)
        for src, fhash in new_files:
            copy_single_file(config, session, src, fhash)
        session.commit()

        # Add a new file to the recorder
        (recorder_dir / "memo3.mp3").write_bytes(b"new audio data")
        recorder_files = scan_recorder(config)

        # Only the new file should be hashed
        with patch("easytrans.sync.compute_file_hash", wraps=compute_file_hash) as mock_hash:
            new = find_new_files(session, recorder_files)
            assert len(new) == 1
            assert new[0][0].name == "memo3.mp3"
            # Only called once (for memo3.mp3), not for memo1/memo2
            assert mock_hash.call_count == 1


def test_copy_single_file_stores_source_metadata(tmp_path: Path) -> None:
    """copy_single_file should create a SourceFile entry for the copied file."""
    recorder_dir = _setup_recorder(tmp_path)
    config = _make_config(tmp_path, recorder_dir)
    config.ensure_dirs()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        src = recorder_dir / "memo1.mp3"
        file_hash = compute_file_hash(src)
        stat = src.stat()

        copy_single_file(config, session, src, file_hash)
        session.commit()

        sf = session.query(SourceFile).one()
        assert sf.filename == "memo1.mp3"
        assert sf.file_size == stat.st_size
        assert sf.mtime_ns == stat.st_mtime_ns
        assert sf.file_hash == file_hash
