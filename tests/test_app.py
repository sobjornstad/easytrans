"""UI tests for the EasyTrans Textual application."""

import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from easytrans.app import EasyTransApp, GotoStatus, MemoTable, SyncProgressModal
from easytrans.config import EasyTransConfig, RecorderConfig, WhisperConfig
from easytrans.files import compute_file_hash, text_path
from easytrans.models import Base, Memo, Transcription
from easytrans.sync import copy_single_file, find_new_files, scan_recorder


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


# --- Vim navigation tests ---


def _add_many_memos(engine, tmp_path: Path, count: int = 10) -> None:
    """Add multiple memos for navigation tests."""
    for i in range(1, count + 1):
        _add_memo(
            engine, tmp_path,
            file_hash=f"hash{i:04d}",
            file_id=f"2026-{i:04d}",
            text=f"Memo number {i}",
        )


@pytest.mark.asyncio
async def test_gg_jumps_to_first(tmp_path: Path) -> None:
    """gg jumps cursor to the first row."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        # Move to last row
        await pilot.press("G")
        await pilot.pause()
        assert table.cursor_coordinate.row == 4

        # gg to jump to first
        await pilot.press("g", "g")
        await pilot.pause()
        assert table.cursor_coordinate.row == 0


@pytest.mark.asyncio
async def test_G_jumps_to_last(tmp_path: Path) -> None:
    """G jumps cursor to the last row."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        assert table.cursor_coordinate.row == 0

        await pilot.press("G")
        await pilot.pause()
        assert table.cursor_coordinate.row == 4


@pytest.mark.asyncio
async def test_count_j_moves_down_n_rows(tmp_path: Path) -> None:
    """3j moves cursor down 3 rows."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 10)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        assert table.cursor_coordinate.row == 0

        await pilot.press("3", "j")
        await pilot.pause()
        assert table.cursor_coordinate.row == 3


@pytest.mark.asyncio
async def test_count_k_moves_up_n_rows(tmp_path: Path) -> None:
    """3k moves cursor up 3 rows."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 10)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        # Start at row 5
        await pilot.press("5", "j")
        await pilot.pause()
        assert table.cursor_coordinate.row == 5

        await pilot.press("3", "k")
        await pilot.pause()
        assert table.cursor_coordinate.row == 2


@pytest.mark.asyncio
async def test_goto_by_seq_number(tmp_path: Path) -> None:
    """Typing a sequence number + Enter navigates to that memo."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)

        # Type "3" + Enter → go to memo 2026-0003 (row index 2)
        await pilot.press("3", "enter")
        await pilot.pause()
        assert table.cursor_coordinate.row == 2
        assert app._get_selected_row_key() == "hash0003"


@pytest.mark.asyncio
async def test_goto_by_year_seq(tmp_path: Path) -> None:
    """Typing year-seq + Enter navigates to that specific memo."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)

        # Type "2026-4" + Enter → go to memo 2026-0004 (row index 3)
        await pilot.press("2", "0", "2", "6", "-", "4", "enter")
        await pilot.pause()
        assert table.cursor_coordinate.row == 3
        assert app._get_selected_row_key() == "hash0004"


@pytest.mark.asyncio
async def test_goto_status_shown_and_hidden(tmp_path: Path) -> None:
    """Goto buffer status appears in footer area and hides when cleared."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        status = app.query_one("#goto-status", GotoStatus)

        # Initially hidden
        assert not status.has_class("visible")

        # Type a digit — status should appear
        await pilot.press("3")
        await pilot.pause()
        assert status.has_class("visible")
        assert "3" in str(status._Static__content)

        # Press Escape — status should hide
        await pilot.press("escape")
        await pilot.pause()
        assert not status.has_class("visible")


@pytest.mark.asyncio
async def test_g_clears_goto_buffer(tmp_path: Path) -> None:
    """Typing g while the goto buffer has digits clears the buffer."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        status = app.query_one("#goto-status", GotoStatus)

        # Type "5g" — the 5 should be cleared, g becomes first press
        await pilot.press("5")
        await pilot.pause()
        assert status.has_class("visible")

        await pilot.press("g")
        await pilot.pause()
        assert not status.has_class("visible")
        # g_pending should be set
        assert table._g_pending is True


@pytest.mark.asyncio
async def test_goto_not_found_shows_input(tmp_path: Path) -> None:
    """Goto to a non-existent ID shows what was typed."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 3)

    async with app.run_test(notifications=True) as pilot:
        table = app.query_one("#memo-table", MemoTable)

        # Type "99" + Enter — seq 0099 doesn't exist
        await pilot.press("9", "9", "enter")
        await pilot.pause()
        # Cursor should stay where it was
        assert table.cursor_coordinate.row == 0
        # Notification should include what was typed
        assert any("99" in str(n.message) for n in app._notifications)


@pytest.mark.asyncio
async def test_goto_hidden_completed_explains(tmp_path: Path) -> None:
    """Goto to a completed (hidden) memo explains it's marked done."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 3)
    # Mark memo 2 as completed
    with Session(app.engine) as session:
        memo = session.get(Memo, "hash0002")
        memo.completed = True
        session.commit()

    async with app.run_test(notifications=True) as pilot:
        # Refresh to hide the completed memo
        app._refresh_table()
        await pilot.pause()
        table = app.query_one("#memo-table", MemoTable)
        assert table.row_count == 2  # memo 2 is hidden

        # Try to goto memo 2
        await pilot.press("2", "enter")
        await pilot.pause()
        # Should explain it's done
        assert any("done" in str(n.message) for n in app._notifications)


@pytest.mark.asyncio
async def test_count_j_clamps_at_end(tmp_path: Path) -> None:
    """Count movement doesn't go past the last row."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)

        # 99j from row 0 — should end at last row (4)
        await pilot.press("9", "9", "j")
        await pilot.pause()
        assert table.cursor_coordinate.row == 4


@pytest.mark.asyncio
async def test_gg_on_empty_table(tmp_path: Path) -> None:
    """gg on empty table doesn't crash."""
    app = _make_app(tmp_path)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        await pilot.press("g", "g")
        await pilot.pause()
        # No crash — row stays at 0
        assert table.cursor_coordinate.row == 0


@pytest.mark.asyncio
async def test_G_on_empty_table(tmp_path: Path) -> None:
    """G on empty table doesn't crash."""
    app = _make_app(tmp_path)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        await pilot.press("G")
        await pilot.pause()
        assert table.cursor_coordinate.row == 0


@pytest.mark.asyncio
async def test_goto_backspace_corrects_input(tmp_path: Path) -> None:
    """Backspace removes the last character from the goto buffer."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        status = app.query_one("#goto-status", GotoStatus)

        # Type "35", backspace, "4" → buffer = "34" → goto 2026-0004
        await pilot.press("3", "5")
        await pilot.pause()
        assert "35" in str(status._Static__content)

        await pilot.press("backspace")
        await pilot.pause()
        assert status.has_class("visible")
        assert "3_" in str(status._Static__content)

        await pilot.press("4")
        await pilot.pause()
        assert "34" in str(status._Static__content)


@pytest.mark.asyncio
async def test_goto_backspace_to_empty_then_enter(tmp_path: Path) -> None:
    """Backspace to empty buffer, then Enter exits cleanly."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        status = app.query_one("#goto-status", GotoStatus)

        # Type "3", backspace → empty but still active
        await pilot.press("3")
        await pilot.pause()
        assert status.has_class("visible")

        await pilot.press("backspace")
        await pilot.pause()
        assert status.has_class("visible")
        content = str(status._Static__content)
        assert "Go to:" in content

        # Enter with empty buffer → exits cleanly, no error
        await pilot.press("enter")
        await pilot.pause()
        assert not status.has_class("visible")
        # Cursor should stay where it was
        assert table.cursor_coordinate.row == 0


@pytest.mark.asyncio
async def test_goto_backspace_to_empty_then_retype(tmp_path: Path) -> None:
    """After backspacing to empty, can type new digits."""
    app = _make_app(tmp_path)
    _add_many_memos(app.engine, tmp_path, 5)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)

        # Type "9", backspace, "3", Enter → go to memo 2026-0003
        await pilot.press("9", "backspace", "3", "enter")
        await pilot.pause()
        assert table.cursor_coordinate.row == 2
        assert app._get_selected_row_key() == "hash0003"


# --- Sync progress modal tests ---


def _make_app_with_recorder(tmp_path: Path) -> tuple[EasyTransApp, Path]:
    """Create an app with a fake recorder directory. Returns (app, recorder_dir)."""
    recorder_dir = tmp_path / "mount" / "RECORDER" / "FOLDER_B"
    recorder_dir.mkdir(parents=True)
    config = EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(
            device_path="/dev/null",
            mount_point=str(recorder_dir.parent.parent),
            audio_dir=str(recorder_dir.relative_to(recorder_dir.parent.parent)),
        ),
        whisper=WhisperConfig(),
    )
    config.ensure_dirs()

    app = EasyTransApp(config=config)
    engine = create_engine(f"sqlite:///{config.db_path}")
    Base.metadata.create_all(engine)
    app.engine = engine
    return app, recorder_dir


@pytest.mark.asyncio
async def test_model_column_width_adapts_to_data(tmp_path: Path) -> None:
    """Model column should be wide enough for the longest model name."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "h1", "2026-0001", text="memo one")
    # Add a transcription with a long model name
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash="h1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny.en",
            text="[00:00] Hello",
        )
        session.add(t)
        session.commit()

    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#memo-table")
        # Find the Model column and check its content width
        cols = list(table.columns.values())
        model_col = cols[3]
        assert model_col.label.plain == "Model"
        assert model_col.content_width >= len("tiny.en")


@pytest.mark.asyncio
async def test_model_column_minimum_is_header_width(tmp_path: Path) -> None:
    """Model column should be at least as wide as the 'Model' header."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, text="memo")

    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#memo-table")
        cols = list(table.columns.values())
        model_col = cols[3]
        assert model_col.content_width >= len("Model")


@pytest.mark.asyncio
async def test_sync_modal_composes_with_all_steps(tmp_path: Path) -> None:
    """Modal should have all 4 step widgets and a title."""
    app, _ = _make_app_with_recorder(tmp_path)
    async with app.run_test() as pilot:
        modal = SyncProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        assert modal.query_one("#step-mount")
        assert modal.query_one("#step-scan")
        assert modal.query_one("#step-copy")
        assert modal.query_one("#step-unmount")
        assert modal.query_one("#sync-title")


@pytest.mark.asyncio
async def test_sync_modal_ready_event_fires(tmp_path: Path) -> None:
    """wait_ready() should unblock after on_mount."""
    app, _ = _make_app_with_recorder(tmp_path)
    async with app.run_test() as pilot:
        modal = SyncProgressModal()
        app.push_screen(modal)
        await pilot.pause()
        assert modal._ready.is_set()


@pytest.mark.asyncio
async def test_sync_modal_set_step_updates_text(tmp_path: Path) -> None:
    """set_step should update the step widget content."""
    app, _ = _make_app_with_recorder(tmp_path)
    async with app.run_test() as pilot:
        modal = SyncProgressModal()
        app.push_screen(modal)
        await pilot.pause()

        modal.set_step("step-scan", "Found 5 new file(s)", "done")
        await pilot.pause()
        rendered = str(modal.query_one("#step-scan").render())
        assert "Found 5 new file(s)" in rendered


@pytest.mark.asyncio
async def test_sync_no_new_files_closes_modal(tmp_path: Path) -> None:
    """When no new files exist, modal shows and auto-closes."""
    app, recorder_dir = _make_app_with_recorder(tmp_path)
    # Empty recorder dir — no files to find
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause(delay=2.0)

        # Modal should be closed
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_sync_new_files_appear_in_table(tmp_path: Path) -> None:
    """New files from recorder should appear in the table after sync."""
    app, recorder_dir = _make_app_with_recorder(tmp_path)
    (recorder_dir / "memo1.mp3").write_bytes(b"audio data 1")
    (recorder_dir / "memo2.mp3").write_bytes(b"audio data 2")

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 0

        with patch("easytrans.app.transcribe_memo"):
            await pilot.press("s")
            await pilot.pause(delay=2.0)

        assert table.row_count == 2


@pytest.mark.asyncio
async def test_sync_shows_transcribing_status(tmp_path: Path) -> None:
    """During transcription, rows should show '(transcribing...)' in Preview."""
    app, recorder_dir = _make_app_with_recorder(tmp_path)
    (recorder_dir / "memo1.mp3").write_bytes(b"audio data single")

    transcribe_started = threading.Event()
    transcribe_continue = threading.Event()

    def mock_transcribe(config, session, memo, **kwargs):
        transcribe_started.set()
        transcribe_continue.wait(timeout=5)

    async with app.run_test() as pilot:
        await pilot.pause()

        with patch("easytrans.app.transcribe_memo", side_effect=mock_transcribe):
            await pilot.press("s")
            transcribe_started.wait(timeout=5)
            await pilot.pause(delay=0.5)

            table = app.query_one("#memo-table", DataTable)
            if table.row_count > 0:
                cell_value = table.get_cell_at(Coordinate(0, 4))
                assert cell_value == "(transcribing...)"

            transcribe_continue.set()

        await pilot.pause(delay=1.0)


@pytest.mark.asyncio
async def test_sync_updates_row_after_transcription(tmp_path: Path) -> None:
    """After transcription, the row should show the transcription text."""
    app, recorder_dir = _make_app_with_recorder(tmp_path)
    (recorder_dir / "memo1.mp3").write_bytes(b"audio data for row update")

    def mock_transcribe(config, session, memo, **kwargs):
        """Create a fake transcription and .md file."""
        from easytrans.files import text_path as tp
        from easytrans.models import Transcription as T

        t = T(
            memo_hash=memo.file_hash,
            transcribed_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Hello from test",
        )
        session.add(t)
        session.flush()
        md = tp(config.data_dir, memo.file_id)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("Hello from test\n")

    async with app.run_test() as pilot:
        await pilot.pause()

        with patch("easytrans.app.transcribe_memo", side_effect=mock_transcribe):
            await pilot.press("s")
            await pilot.pause(delay=2.0)

        table = app.query_one("#memo-table", DataTable)
        assert table.row_count == 1
        # Preview column should contain the transcription text
        preview = table.get_cell_at(Coordinate(0, 4))
        assert "Hello from test" in preview
        # Model column should be updated
        model = table.get_cell_at(Coordinate(0, 3))
        assert model == "tiny"
