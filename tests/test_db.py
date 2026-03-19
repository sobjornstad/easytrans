"""Tests for database operations."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from easytrans.db import (
    get_memos,
    get_memos_needing_upgrade,
    get_transcriptions,
    get_latest_transcription,
    get_untranscribed_memos,
    hash_exists,
)
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


def test_get_untranscribed_memos_returns_memos_without_transcriptions(
    db_session: Session,
) -> None:
    m1 = _make_memo("hash1", "2026-0001")
    m2 = _make_memo("hash2", "2026-0002")
    db_session.add_all([m1, m2])
    db_session.flush()

    result = get_untranscribed_memos(db_session)
    assert len(result) == 2
    assert [m.file_id for m in result] == ["2026-0001", "2026-0002"]


def test_get_untranscribed_memos_excludes_transcribed(db_session: Session) -> None:
    m1 = _make_memo("hash1", "2026-0001")
    m2 = _make_memo("hash2", "2026-0002")
    db_session.add_all([m1, m2])
    db_session.flush()

    # Add a transcription for m1 only
    t = Transcription(
        memo_hash="hash1",
        transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
        model_name="tiny",
        text="hello",
    )
    db_session.add(t)
    db_session.flush()

    result = get_untranscribed_memos(db_session)
    assert len(result) == 1
    assert result[0].file_hash == "hash2"


def test_get_untranscribed_memos_empty_when_all_transcribed(
    db_session: Session,
) -> None:
    m1 = _make_memo("hash1", "2026-0001")
    db_session.add(m1)
    db_session.flush()

    t = Transcription(
        memo_hash="hash1",
        transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
        model_name="tiny",
        text="hello",
    )
    db_session.add(t)
    db_session.flush()

    result = get_untranscribed_memos(db_session)
    assert len(result) == 0


def test_get_untranscribed_memos_ordered_by_file_id(db_session: Session) -> None:
    m1 = _make_memo("hash1", "2026-0003")
    m2 = _make_memo("hash2", "2026-0001")
    m3 = _make_memo("hash3", "2026-0002")
    db_session.add_all([m1, m2, m3])
    db_session.flush()

    result = get_untranscribed_memos(db_session)
    assert [m.file_id for m in result] == ["2026-0001", "2026-0002", "2026-0003"]


# --- Tests for get_memos_needing_upgrade ---


def _add_transcription(
    db_session: Session,
    memo_hash: str,
    model_name: str,
    hour: int = 13,
) -> None:
    """Helper to add a transcription with a given model."""
    t = Transcription(
        memo_hash=memo_hash,
        transcribed_at=datetime(2026, 1, 15, hour, 0, tzinfo=timezone.utc),
        model_name=model_name,
        text=f"transcribed by {model_name}",
    )
    db_session.add(t)
    db_session.flush()


def test_upgrade_returns_memos_with_only_default_model(db_session: Session) -> None:
    """Memos with only a 'tiny' transcription need upgrading."""
    m1 = _make_memo("hash1", "2026-0001")
    m2 = _make_memo("hash2", "2026-0002")
    db_session.add_all([m1, m2])
    db_session.flush()
    _add_transcription(db_session, "hash1", "tiny")
    _add_transcription(db_session, "hash2", "tiny")

    result = get_memos_needing_upgrade(db_session, "small", "medium")
    assert [m.file_hash for m in result] == ["hash1", "hash2"]


def test_upgrade_excludes_memos_with_mid_model(db_session: Session) -> None:
    """Memos already having a mid_model transcription are excluded."""
    m1 = _make_memo("hash1", "2026-0001")
    db_session.add(m1)
    db_session.flush()
    _add_transcription(db_session, "hash1", "tiny")
    _add_transcription(db_session, "hash1", "small", hour=14)

    result = get_memos_needing_upgrade(db_session, "small", "medium")
    assert len(result) == 0


def test_upgrade_excludes_memos_with_large_model(db_session: Session) -> None:
    """Memos already having a large_model transcription are excluded."""
    m1 = _make_memo("hash1", "2026-0001")
    db_session.add(m1)
    db_session.flush()
    _add_transcription(db_session, "hash1", "tiny")
    _add_transcription(db_session, "hash1", "medium", hour=14)

    result = get_memos_needing_upgrade(db_session, "small", "medium")
    assert len(result) == 0


def test_upgrade_excludes_untranscribed_memos(db_session: Session) -> None:
    """Memos with no transcriptions at all (tier 1 not done) are excluded."""
    m1 = _make_memo("hash1", "2026-0001")
    db_session.add(m1)
    db_session.flush()

    result = get_memos_needing_upgrade(db_session, "small", "medium")
    assert len(result) == 0


def test_upgrade_empty_when_all_upgraded(db_session: Session) -> None:
    """Returns empty list when all memos already have mid or large model."""
    m1 = _make_memo("hash1", "2026-0001")
    m2 = _make_memo("hash2", "2026-0002")
    db_session.add_all([m1, m2])
    db_session.flush()
    _add_transcription(db_session, "hash1", "tiny")
    _add_transcription(db_session, "hash1", "small", hour=14)
    _add_transcription(db_session, "hash2", "tiny")
    _add_transcription(db_session, "hash2", "medium", hour=14)

    result = get_memos_needing_upgrade(db_session, "small", "medium")
    assert len(result) == 0


def test_upgrade_ordered_by_file_id(db_session: Session) -> None:
    """Results are ordered by file_id."""
    m1 = _make_memo("hash1", "2026-0003")
    m2 = _make_memo("hash2", "2026-0001")
    m3 = _make_memo("hash3", "2026-0002")
    db_session.add_all([m1, m2, m3])
    db_session.flush()
    _add_transcription(db_session, "hash1", "tiny")
    _add_transcription(db_session, "hash2", "tiny")
    _add_transcription(db_session, "hash3", "tiny")

    result = get_memos_needing_upgrade(db_session, "small", "medium")
    assert [m.file_id for m in result] == ["2026-0001", "2026-0002", "2026-0003"]
