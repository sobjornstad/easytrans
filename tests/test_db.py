"""Tests for database operations."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from easytrans.db import get_memos, get_transcriptions, get_latest_transcription, hash_exists
from easytrans.models import Memo, Transcription


def _make_memo(file_hash: str = "abc123", file_id: str = "2026-0001") -> Memo:
    return Memo(
        file_hash=file_hash,
        file_id=file_id,
        recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        synced_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        completed=False,
    )


def test_hash_exists_false(db_session: Session) -> None:
    assert hash_exists(db_session, "nonexistent") is False


def test_hash_exists_true(db_session: Session) -> None:
    memo = _make_memo()
    db_session.add(memo)
    db_session.flush()
    assert hash_exists(db_session, "abc123") is True


def test_get_memos_excludes_completed(db_session: Session) -> None:
    m1 = _make_memo("hash1", "2026-0001")
    m2 = _make_memo("hash2", "2026-0002")
    m2.completed = True
    db_session.add_all([m1, m2])
    db_session.flush()

    memos = get_memos(db_session, include_completed=False)
    assert len(memos) == 1
    assert memos[0].file_id == "2026-0001"


def test_get_memos_includes_completed(db_session: Session) -> None:
    m1 = _make_memo("hash1", "2026-0001")
    m2 = _make_memo("hash2", "2026-0002")
    m2.completed = True
    db_session.add_all([m1, m2])
    db_session.flush()

    memos = get_memos(db_session, include_completed=True)
    assert len(memos) == 2


def test_get_memos_ordered_by_file_id(db_session: Session) -> None:
    m1 = _make_memo("hash1", "2026-0003")
    m2 = _make_memo("hash2", "2026-0001")
    db_session.add_all([m1, m2])
    db_session.flush()

    memos = get_memos(db_session, include_completed=True)
    assert [m.file_id for m in memos] == ["2026-0001", "2026-0003"]


def test_get_transcriptions(db_session: Session) -> None:
    memo = _make_memo()
    db_session.add(memo)
    db_session.flush()

    t1 = Transcription(
        memo_hash="abc123",
        transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
        model_name="tiny",
        text="hello world",
    )
    t2 = Transcription(
        memo_hash="abc123",
        transcribed_at=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
        model_name="medium",
        text="hello world improved",
    )
    db_session.add_all([t1, t2])
    db_session.flush()

    transcriptions = get_transcriptions(db_session, "abc123")
    assert len(transcriptions) == 2
    assert transcriptions[0].model_name == "tiny"
    assert transcriptions[1].model_name == "medium"


def test_get_latest_transcription(db_session: Session) -> None:
    memo = _make_memo()
    db_session.add(memo)
    db_session.flush()

    t1 = Transcription(
        memo_hash="abc123",
        transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
        model_name="tiny",
        text="first",
    )
    t2 = Transcription(
        memo_hash="abc123",
        transcribed_at=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
        model_name="medium",
        text="second",
    )
    db_session.add_all([t1, t2])
    db_session.flush()

    latest = get_latest_transcription(db_session, "abc123")
    assert latest is not None
    assert latest.model_name == "medium"


def test_get_latest_transcription_none(db_session: Session) -> None:
    assert get_latest_transcription(db_session, "nonexistent") is None
