"""Database session management for EasyTrans."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, exists, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from easytrans.models import Base, Memo, Transcription


def get_engine(db_path: Path) -> Engine:
    """Create a SQLAlchemy engine and run Alembic migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    run_migrations(engine)
    return engine


def run_migrations(engine: Engine) -> None:
    """Run Alembic migrations to bring the database up to date."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "easytrans:migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))

    with engine.begin() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")


@contextmanager
def get_session(engine: Engine) -> Iterator[Session]:
    """Provide a transactional database session."""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def hash_exists(session: Session, file_hash: str) -> bool:
    """Check if a file hash already exists in the database."""
    return session.get(Memo, file_hash) is not None


def get_memos(session: Session, include_completed: bool = False) -> list[Memo]:
    """Get all memos, optionally including completed ones."""
    stmt = select(Memo).order_by(Memo.file_id)
    if not include_completed:
        stmt = stmt.where(Memo.completed == False)  # noqa: E712
    return list(session.scalars(stmt).all())


def get_transcriptions(session: Session, memo_hash: str) -> list[Transcription]:
    """Get all transcriptions for a memo, ordered by date."""
    stmt = (
        select(Transcription)
        .where(Transcription.memo_hash == memo_hash)
        .order_by(Transcription.transcribed_at)
    )
    return list(session.scalars(stmt).all())


def get_untranscribed_memos(session: Session) -> list[Memo]:
    """Get all memos that have no transcriptions, ordered by file_id."""
    stmt = (
        select(Memo)
        .where(
            ~exists(
                select(Transcription.id).where(
                    Transcription.memo_hash == Memo.file_hash
                )
            )
        )
        .order_by(Memo.file_id)
    )
    return list(session.scalars(stmt).all())


def get_latest_transcription(session: Session, memo_hash: str) -> Transcription | None:
    """Get the most recent transcription for a memo."""
    stmt = (
        select(Transcription)
        .where(Transcription.memo_hash == memo_hash)
        .order_by(Transcription.transcribed_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()
