"""Tests for the shared import_audio_as_memo primitive."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig, RecorderConfig, WhisperConfig
from easytrans.files import compute_file_hash
from easytrans.importer import import_audio_as_memo
from easytrans.models import Base, Memo, SourceFile


def _config(tmp_path: Path) -> EasyTransConfig:
    cfg = EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(),
        whisper=WhisperConfig(),
    )
    cfg.ensure_dirs()
    return cfg


def _session(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_import_copies_file_and_creates_memo(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    src = tmp_path / "incoming.mp3"
    src.write_bytes(b"fake audio data")
    file_hash = compute_file_hash(src)
    recorded_at = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

    with _session(tmp_path) as session:
        memo = import_audio_as_memo(
            cfg, session, src, file_hash, recorded_at,
        )
        session.commit()

        assert memo.file_hash == file_hash
        assert memo.file_id == "2026-0001"
        # SQLite DateTime strips tzinfo on round-trip, so compare naive.
        assert memo.recorded_at.replace(tzinfo=None) == recorded_at.replace(tzinfo=None)
        assert memo.synced_at is not None
        assert memo.completed is False

        dest = cfg.audio_dir / "2026" / "2026-0001.mp3"
        assert dest.exists()
        assert dest.read_bytes() == b"fake audio data"
        # Default mode is copy — the original must still exist.
        assert src.exists()

        # No SourceFile row is written by the import primitive.
        assert session.query(SourceFile).count() == 0


def test_import_move_mode_removes_source(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    src = tmp_path / "incoming.wav"
    src.write_bytes(b"wav bytes")
    file_hash = compute_file_hash(src)
    recorded_at = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

    with _session(tmp_path) as session:
        memo = import_audio_as_memo(
            cfg, session, src, file_hash, recorded_at, move=True,
        )
        session.commit()

        dest = cfg.audio_dir / "2026" / f"{memo.file_id}.wav"
        assert dest.exists()
        assert not src.exists()


def test_import_allocates_sequential_ids(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    recorded_at = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

    with _session(tmp_path) as session:
        first = tmp_path / "a.wav"
        first.write_bytes(b"a")
        second = tmp_path / "b.wav"
        second.write_bytes(b"b")

        m1 = import_audio_as_memo(
            cfg, session, first, compute_file_hash(first), recorded_at,
        )
        m2 = import_audio_as_memo(
            cfg, session, second, compute_file_hash(second), recorded_at,
        )
        session.commit()

        assert m1.file_id == "2026-0001"
        assert m2.file_id == "2026-0002"


def test_import_uses_recorded_at_year_for_slot(tmp_path: Path) -> None:
    """File IDs must reflect recorded_at's year, not the clock."""
    cfg = _config(tmp_path)
    src = tmp_path / "old.mp3"
    src.write_bytes(b"x")
    past = datetime(2024, 1, 1, tzinfo=timezone.utc)

    with _session(tmp_path) as session:
        memo = import_audio_as_memo(
            cfg, session, src, compute_file_hash(src), past,
        )
        session.commit()
        assert memo.file_id.startswith("2024-")
        assert (cfg.audio_dir / "2024" / f"{memo.file_id}.mp3").exists()
