"""UI tests for the EasyTrans Textual application."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from textual.widgets import DataTable

from easytrans.app import EasyTransApp
from easytrans.config import EasyTransConfig, RecorderConfig, WhisperConfig
from easytrans.files import text_path
from easytrans.models import Base, Memo, Transcription


def _make_app(tmp_path: Path) -> EasyTransApp:
    """Create an app with a temporary data directory and in-memory-ish DB."""
    config = EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(),
        whisper=WhisperConfig(),
    )
    config.ensure_dirs()

    app = EasyTransApp(config=config)
    # Use a file-based DB in the data dir so the app can find it
    engine = create_engine(f"sqlite:///{config.db_path}")
    Base.metadata.create_all(engine)
    app.engine = engine
    return app


def _add_memo(
    engine,
    tmp_path: Path,
    file_hash: str = "abc123",
    file_id: str = "2026-0001",
    completed: bool = False,
    text: str = "Hello world transcription",
) -> None:
    """Add a memo and its .md file for testing."""
    with Session(engine) as session:
        memo = Memo(
            file_hash=file_hash,
            file_id=file_id,
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
            synced_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
            completed=completed,
        )
        session.add(memo)
        session.commit()

    # Write .md file
    data_dir = tmp_path / "data"
    md = text_path(data_dir, file_id)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(text + "\n")


@pytest.mark.asyncio
async def test_app_starts(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_app_shows_memos(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_app_hides_completed(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001", completed=False)
    _add_memo(app.engine, tmp_path, "hash2", "2026-0002", completed=True)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 1  # Only incomplete shown

        await pilot.press("h")
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 2  # Both shown


@pytest.mark.asyncio
async def test_mark_complete_stays_visible(tmp_path: Path) -> None:
    """Marking complete keeps the row visible this session (with red indicator)."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 1

        await pilot.press("d")
        await pilot.pause()
        # Row should still be visible (session-completed)
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 1
        assert "abc123" in app._session_completed


@pytest.mark.asyncio
async def test_mark_complete_hidden_on_fresh_start(tmp_path: Path) -> None:
    """Completed memos are hidden when the app starts fresh."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, completed=True)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 0  # hidden by default on fresh start


@pytest.mark.asyncio
async def test_cursor_preserved_on_toggle(tmp_path: Path) -> None:
    """Cursor stays on the same memo when toggling show completed."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001")
    _add_memo(app.engine, tmp_path, "hash2", "2026-0002")
    _add_memo(app.engine, tmp_path, "hash3", "2026-0003")

    async with app.run_test() as pilot:
        # Move to second row
        await pilot.press("j")
        await pilot.pause()
        assert app._get_selected_row_key() == "hash2"

        # Toggle completed — cursor should stay on hash2
        await pilot.press("h")
        await pilot.pause()
        assert app._get_selected_row_key() == "hash2"


@pytest.mark.asyncio
async def test_mark_complete_advances_cursor(tmp_path: Path) -> None:
    """After marking a memo complete, cursor moves to the next row."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001")
    _add_memo(app.engine, tmp_path, "hash2", "2026-0002")
    _add_memo(app.engine, tmp_path, "hash3", "2026-0003")

    async with app.run_test() as pilot:
        # Cursor starts on first row
        assert app._get_selected_row_key() == "hash1"

        await pilot.press("d")
        await pilot.pause()
        # Should advance to second row
        assert app._get_selected_row_key() == "hash2"


@pytest.mark.asyncio
async def test_mark_complete_stays_on_last_row(tmp_path: Path) -> None:
    """Marking the last row complete does not move cursor past the end."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001")
    _add_memo(app.engine, tmp_path, "hash2", "2026-0002")

    async with app.run_test() as pilot:
        # Move to last row
        await pilot.press("j")
        await pilot.pause()
        assert app._get_selected_row_key() == "hash2"

        await pilot.press("d")
        await pilot.pause()
        # Should stay on last row (hash2)
        assert app._get_selected_row_key() == "hash2"


@pytest.mark.asyncio
async def test_column_order_wide(tmp_path: Path) -> None:
    """On a wide terminal, all columns including dates are shown."""
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#memo-table", DataTable)
        labels = [col.label.plain for col in table.columns.values()]
        assert labels == ["", "ID", "Length", "Model", "Preview", "Recorded", "Transcribed"]


@pytest.mark.asyncio
async def test_date_columns_hidden_narrow(tmp_path: Path) -> None:
    """On a narrow terminal, date columns are hidden."""
    app = _make_app(tmp_path)
    # Width 80: available=78, preview_with_dates=78-30-36-2=10 < 20 → hide dates
    async with app.run_test(size=(80, 40)) as pilot:
        table = app.query_one("#memo-table", DataTable)
        labels = [col.label.plain for col in table.columns.values()]
        assert "Recorded" not in labels
        assert "Transcribed" not in labels
        assert labels == ["", "ID", "Length", "Model", "Preview"]


@pytest.mark.asyncio
async def test_dates_in_preview_when_columns_hidden(tmp_path: Path) -> None:
    """When date columns are hidden, dates appear in the preview pane."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path)
    # Add a transcription so we have a transcribed_at date
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash="abc123",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Hello",
        )
        session.add(t)
        session.commit()

    async with app.run_test(size=(80, 40)) as pilot:
        from easytrans.app import MemoPreview
        preview = app.query_one("#preview", MemoPreview)
        content = str(preview._Static__content)
        assert "Recorded: 2026-01-15 10:00" in content
        assert "Transcribed: 2026-01-15 13:00" in content


def test_strip_front_matter() -> None:
    text = "---\nid: 2026-0001\nstatus: pending\nrecorded: 2026-01-15 10:00\n---\n\nHello world\n"
    result = EasyTransApp._strip_front_matter(text)
    assert result == "Hello world\n"


def test_strip_front_matter_no_front_matter() -> None:
    text = "Hello world\n"
    result = EasyTransApp._strip_front_matter(text)
    assert result == "Hello world\n"


def test_build_front_matter() -> None:
    app = EasyTransApp.__new__(EasyTransApp)
    memo = Memo(
        file_hash="abc",
        file_id="2026-0001",
        recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        synced_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        duration_seconds=95.0,
        completed=True,
    )
    fm = app._build_front_matter(memo)
    assert "id: 2026-0001" in fm
    assert "status: done" in fm
    assert "length: 1:35" in fm
    assert fm.startswith("---\n")
    assert "---\n\n" in fm  # blank line after closing ---


@pytest.mark.asyncio
async def test_timestamp_toggle(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path)
    # Add a transcription so timestamps are available
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash="abc123",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Hello\n[00:05] world",
        )
        session.add(t)
        session.commit()

    async with app.run_test() as pilot:
        from easytrans.app import MemoPreview
        preview = app.query_one("#preview", MemoPreview)
        # Default: show .md text (no timestamps)
        content = str(preview._Static__content)
        assert "Hello world" in content

        await pilot.press("t")
        await pilot.pause()
        # After toggle: show timestamped text from DB
        content = str(preview._Static__content)
        assert "[00:00]" in content
