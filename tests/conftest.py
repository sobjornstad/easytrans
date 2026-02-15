"""Shared test fixtures for EasyTrans."""

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from easytrans.models import Base


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory structure."""
    (tmp_path / "audio").mkdir()
    (tmp_path / "text").mkdir()
    return tmp_path


@pytest.fixture
def db_engine(tmp_data_dir: Path):
    """Create an in-memory SQLite engine with tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine) -> Session:
    """Create a database session for testing."""
    with Session(db_engine) as session:
        yield session
