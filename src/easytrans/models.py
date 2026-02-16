"""SQLAlchemy ORM models for EasyTrans."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Memo(Base):
    __tablename__ = "memos"

    file_hash: Mapped[str] = mapped_column(String, primary_key=True)
    file_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    transcriptions: Mapped[list["Transcription"]] = relationship(
        back_populates="memo", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Memo {self.file_id}>"


class SourceFile(Base):
    """Cached mapping from recorder file metadata to content hash.

    Avoids re-reading file contents from USB on repeated syncs.
    """
    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(
        ForeignKey("memos.file_hash"), nullable=False,
    )

    memo: Mapped["Memo"] = relationship()

    def __repr__(self) -> str:
        return f"<SourceFile {self.filename}>"


class Transcription(Base):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memo_hash: Mapped[str] = mapped_column(ForeignKey("memos.file_hash"), nullable=False)
    transcribed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    memo: Mapped["Memo"] = relationship(back_populates="transcriptions")

    def __repr__(self) -> str:
        return f"<Transcription {self.id} ({self.model_name})>"
