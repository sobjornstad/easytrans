"""UI tests for the EasyTrans Textual application."""

import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

from easytrans.app import (
    EasyTransApp,
    GotoStatus,
    MemoPreview,
    MemoTable,
    PlaybackStatus,
    SyncProgressModal,
)
from easytrans.playback import StubAudioPlayer
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
        preview = app.query_one("#preview", MemoPreview)
        content = str(preview.query_one("#preview-text", Static)._Static__content)
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
        preview = app.query_one("#preview", MemoPreview)
        inner = preview.query_one("#preview-text", Static)
        # Default: show .md text (no timestamps)
        content = str(inner._Static__content)
        assert "Hello world" in content

        await pilot.press("t")
        await pilot.pause()
        # After toggle: show timestamped text from DB
        content = str(inner._Static__content)
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
@patch("easytrans.app.unmount_recorder")
@patch("easytrans.app.mount_recorder")
async def test_sync_no_new_files_closes_modal(_mock_mount, _mock_umount, tmp_path: Path) -> None:
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
@patch("easytrans.app.unmount_recorder")
@patch("easytrans.app.mount_recorder")
async def test_sync_new_files_appear_in_table(_mock_mount, _mock_umount, tmp_path: Path) -> None:
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
@patch("easytrans.app.unmount_recorder")
@patch("easytrans.app.mount_recorder")
async def test_sync_shows_transcribing_status(_mock_mount, _mock_umount, tmp_path: Path) -> None:
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
@patch("easytrans.app.unmount_recorder")
@patch("easytrans.app.mount_recorder")
async def test_sync_updates_row_after_transcription(_mock_mount, _mock_umount, tmp_path: Path) -> None:
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


# --- Preview pane scrolling tests ---


def _add_memo_long_text(engine, tmp_path: Path, lines: int = 50) -> None:
    """Add a memo with enough text to overflow the preview pane."""
    text = "\n".join(f"Line {i+1} of the transcription" for i in range(lines))
    _add_memo(engine, tmp_path, text=text)


@pytest.mark.asyncio
async def test_preview_is_focusable(tmp_path: Path) -> None:
    """MemoPreview has can_focus=True and can receive focus via Tab."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path)

    async with app.run_test() as pilot:
        table = app.query_one("#memo-table", MemoTable)
        preview = app.query_one("#preview", MemoPreview)

        # Table should have initial focus
        assert table.has_focus

        # Tab should move focus to preview
        await pilot.press("tab")
        await pilot.pause()
        assert preview.has_focus
        assert not table.has_focus

        # Shift+Tab should return focus to table
        await pilot.press("shift+tab")
        await pilot.pause()
        assert table.has_focus
        assert not preview.has_focus


@pytest.mark.asyncio
async def test_preview_focus_border_changes(tmp_path: Path) -> None:
    """MemoPreview shows a different border when focused."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path)

    async with app.run_test() as pilot:
        preview = app.query_one("#preview", MemoPreview)

        # When not focused, border should be solid
        assert not preview.has_focus

        # When focused, border should be double
        await pilot.press("tab")
        await pilot.pause()
        assert preview.has_focus


@pytest.mark.asyncio
async def test_preview_scroll_down_j(tmp_path: Path) -> None:
    """Pressing j in focused preview scrolls down."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        preview = app.query_one("#preview", MemoPreview)

        # Focus the preview
        await pilot.press("tab")
        await pilot.pause()
        assert preview.has_focus

        initial_scroll = preview.scroll_y

        # Press j to scroll down
        await pilot.press("j")
        await pilot.pause()
        assert preview.scroll_y > initial_scroll


@pytest.mark.asyncio
async def test_preview_scroll_up_k(tmp_path: Path) -> None:
    """Pressing k in focused preview scrolls up."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        preview = app.query_one("#preview", MemoPreview)

        # Focus the preview and scroll down first
        await pilot.press("tab")
        await pilot.pause()
        for _ in range(5):
            await pilot.press("j")
        await pilot.pause()
        scrolled_pos = preview.scroll_y
        assert scrolled_pos > 0

        # Press k to scroll up
        await pilot.press("k")
        await pilot.pause()
        assert preview.scroll_y < scrolled_pos


@pytest.mark.asyncio
async def test_preview_scroll_half_page(tmp_path: Path) -> None:
    """Ctrl+D/Ctrl+U scroll the focused preview by half a page."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        preview = app.query_one("#preview", MemoPreview)

        await pilot.press("tab")
        await pilot.pause()
        assert preview.has_focus

        # Ctrl+D scrolls down
        await pilot.press("ctrl+d")
        await pilot.pause()
        after_down = preview.scroll_y
        assert after_down > 0

        # Ctrl+U scrolls back up
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert preview.scroll_y < after_down


@pytest.mark.asyncio
async def test_preview_scroll_full_page(tmp_path: Path) -> None:
    """Ctrl+F/Ctrl+B scroll the focused preview by a full page."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        preview = app.query_one("#preview", MemoPreview)

        await pilot.press("tab")
        await pilot.pause()

        # Ctrl+F scrolls down a full page
        await pilot.press("ctrl+f")
        await pilot.pause()
        after_page = preview.scroll_y
        assert after_page > 0

        # Ctrl+B scrolls back up
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert preview.scroll_y < after_page


@pytest.mark.asyncio
async def test_preview_scroll_resets_on_row_change(tmp_path: Path) -> None:
    """Selecting a different memo resets preview scroll to top."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)
    _add_memo(app.engine, tmp_path, file_hash="hash2", file_id="2026-0002",
              text="Short second memo")

    async with app.run_test(size=(120, 20)) as pilot:
        preview = app.query_one("#preview", MemoPreview)

        # Focus preview and scroll down
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert preview.scroll_y > 0

        # Switch back to table and move to next row
        await pilot.press("shift+tab")
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()

        # Preview scroll should be reset to top
        assert preview.scroll_y == 0


# --- Scroll-other-pane tests ---


@pytest.mark.asyncio
async def test_scroll_other_ctrl_e_scrolls_preview_down(tmp_path: Path) -> None:
    """Ctrl+E from the table scrolls preview down one line."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one("#memo-table", MemoTable)
        preview = app.query_one("#preview", MemoPreview)

        assert table.has_focus
        initial = preview.scroll_y

        await pilot.press("ctrl+e")
        await pilot.pause()
        assert preview.scroll_y > initial
        # Table should still have focus
        assert table.has_focus


@pytest.mark.asyncio
async def test_scroll_other_ctrl_y_scrolls_preview_up(tmp_path: Path) -> None:
    """Ctrl+Y from the table scrolls preview up one line."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one("#memo-table", MemoTable)
        preview = app.query_one("#preview", MemoPreview)

        # Scroll down first
        for _ in range(5):
            await pilot.press("ctrl+e")
        await pilot.pause()
        after_down = preview.scroll_y
        assert after_down > 0

        # Ctrl+Y scrolls back up
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert preview.scroll_y < after_down
        assert table.has_focus


@pytest.mark.asyncio
async def test_scroll_other_ctrl_v_page_down(tmp_path: Path) -> None:
    """Ctrl+V from the table scrolls preview down one page."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one("#memo-table", MemoTable)
        preview = app.query_one("#preview", MemoPreview)

        assert table.has_focus
        initial = preview.scroll_y

        await pilot.press("ctrl+v")
        await pilot.pause()
        assert preview.scroll_y > initial
        assert table.has_focus


@pytest.mark.asyncio
async def test_scroll_other_alt_v_page_up(tmp_path: Path) -> None:
    """Alt+V from the table scrolls preview up one page."""
    app = _make_app(tmp_path)
    _add_memo_long_text(app.engine, tmp_path, lines=50)

    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one("#memo-table", MemoTable)
        preview = app.query_one("#preview", MemoPreview)

        # Scroll down first
        await pilot.press("ctrl+v")
        await pilot.pause()
        after_page = preview.scroll_y
        assert after_page > 0

        # Alt+V scrolls back up
        # Use escape,v sequence for Alt+V in test environment
        await pilot.press("alt+v")
        await pilot.pause()
        assert preview.scroll_y < after_page
        assert table.has_focus


@pytest.mark.asyncio
async def test_table_j_k_dont_scroll_when_preview_focused(tmp_path: Path) -> None:
    """When preview is focused, j/k scroll preview, not move table cursor."""
    app = _make_app(tmp_path)
    # Add first memo with long text, then more short memos
    long_text = "\n".join(f"Line {i}" for i in range(50))
    _add_memo(app.engine, tmp_path, file_hash="hash0001", file_id="2026-0001",
              text=long_text)
    for i in range(2, 6):
        _add_memo(app.engine, tmp_path, file_hash=f"hash{i:04d}",
                  file_id=f"2026-{i:04d}", text=f"Memo {i}")

    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one("#memo-table", MemoTable)

        # Table cursor starts at row 0
        assert table.cursor_coordinate.row == 0

        # Focus preview
        await pilot.press("tab")
        await pilot.pause()

        # Press j — should scroll preview, NOT move table cursor
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_coordinate.row == 0


# --- Startup transcription tests ---


def _add_memo_no_transcription(
    engine,
    tmp_path: Path,
    file_hash: str = "abc123",
    file_id: str = "2026-0001",
) -> None:
    """Add a memo WITHOUT a transcription or .md file (simulates interrupted sync)."""
    with Session(engine) as session:
        memo = Memo(
            file_hash=file_hash,
            file_id=file_id,
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
            synced_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
            completed=False,
        )
        session.add(memo)
        session.commit()


@pytest.mark.asyncio
async def test_startup_transcribes_untranscribed_memos(tmp_path: Path) -> None:
    """On startup, memos without transcriptions get auto-transcribed."""
    app = _make_app(tmp_path)
    _add_memo_no_transcription(app.engine, tmp_path, "hash1", "2026-0001")
    _add_memo_no_transcription(app.engine, tmp_path, "hash2", "2026-0002")

    transcribed_calls = []

    def mock_transcribe(config, session, memo, **kwargs):
        model = kwargs.get("model_name") or config.whisper.initial_model
        transcribed_calls.append((memo.file_hash, model))
        t = Transcription(
            memo_hash=memo.file_hash,
            transcribed_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            model_name=model,
            text=f"[00:00] Auto transcribed with {model}",
        )
        session.add(t)
        session.flush()
        md = text_path(config.data_dir, memo.file_id)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("Auto transcribed\n")

    with patch("easytrans.app.transcribe_memo", side_effect=mock_transcribe):
        async with app.run_test() as pilot:
            await pilot.pause(delay=3.0)

    # Tier 1 calls (default model)
    tier1 = sorted(h for h, m in transcribed_calls if m == "tiny")
    assert tier1 == ["hash1", "hash2"]
    # Tier 2 calls (mid-model upgrade) should also happen
    tier2 = sorted(h for h, m in transcribed_calls if m == "small")
    assert tier2 == ["hash1", "hash2"]


@pytest.mark.asyncio
async def test_startup_skips_already_transcribed_memos(tmp_path: Path) -> None:
    """On startup, memos that already have transcriptions are NOT re-transcribed at tier 1."""
    app = _make_app(tmp_path)
    # This one has a transcription
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001", text="Already done")
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Already done",
        )
        session.add(t)
        session.commit()

    # This one does NOT have a transcription
    _add_memo_no_transcription(app.engine, tmp_path, "hash2", "2026-0002")

    transcribed_calls = []

    def mock_transcribe(config, session, memo, **kwargs):
        transcribed_calls.append((memo.file_hash, kwargs.get("model_name")))
        model = kwargs.get("model_name") or config.whisper.initial_model
        t = Transcription(
            memo_hash=memo.file_hash,
            transcribed_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            model_name=model,
            text=f"[00:00] Transcribed with {model}",
        )
        session.add(t)
        session.flush()
        md = text_path(config.data_dir, memo.file_id)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(f"Transcribed with {model}\n")

    with patch("easytrans.app.transcribe_memo", side_effect=mock_transcribe):
        async with app.run_test() as pilot:
            await pilot.pause(delay=3.0)

    # Tier 1: only hash2 should have been transcribed with default model
    tier1_calls = [(h, m) for h, m in transcribed_calls if m is None]
    assert tier1_calls == [("hash2", None)]
    # Tier 2 (mid-model): both should get upgraded
    tier2_calls = [(h, m) for h, m in transcribed_calls if m == "small"]
    assert sorted(h for h, _ in tier2_calls) == ["hash1", "hash2"]


@pytest.mark.asyncio
async def test_startup_no_notification_when_all_transcribed(tmp_path: Path) -> None:
    """No notification if all memos already have transcriptions (including mid-model)."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001", text="Done")
    with Session(app.engine) as session:
        t1 = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Done",
        )
        t2 = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            model_name="small",
            text="[00:00] Done upgraded",
        )
        session.add_all([t1, t2])
        session.commit()

    with patch("easytrans.app.transcribe_memo") as mock_tm:
        async with app.run_test(notifications=True) as pilot:
            await pilot.pause(delay=1.0)

    mock_tm.assert_not_called()
    assert not any("pending" in str(n.message) for n in app._notifications)


# --- Default-model upgrade tests ---


@pytest.mark.asyncio
async def test_startup_triggers_default_model_upgrade_after_tier1(tmp_path: Path) -> None:
    """After tier 1 completes on startup, tier 2 default-model upgrade triggers."""
    app = _make_app(tmp_path)
    _add_memo_no_transcription(app.engine, tmp_path, "hash1", "2026-0001")

    transcribed_calls = []

    def mock_transcribe(config, session, memo, **kwargs):
        model = kwargs.get("model_name") or config.whisper.initial_model
        transcribed_calls.append((memo.file_hash, model))
        t = Transcription(
            memo_hash=memo.file_hash,
            transcribed_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            model_name=model,
            text=f"[00:00] Transcribed with {model}",
        )
        session.add(t)
        session.flush()
        md = text_path(config.data_dir, memo.file_id)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(f"Transcribed with {model}\n")

    with patch("easytrans.app.transcribe_memo", side_effect=mock_transcribe):
        async with app.run_test() as pilot:
            await pilot.pause(delay=3.0)

    # Should have tier 1 (tiny) then tier 2 (small)
    assert ("hash1", "tiny") in transcribed_calls
    assert ("hash1", "small") in transcribed_calls


@pytest.mark.asyncio
async def test_startup_triggers_default_model_upgrade_directly(tmp_path: Path) -> None:
    """When no tier 1 work needed, default-model upgrade triggers directly on startup."""
    app = _make_app(tmp_path)
    # Memo already has tier 1 transcription but not default-model
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001", text="Already done")
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Already done",
        )
        session.add(t)
        session.commit()

    transcribed_calls = []

    def mock_transcribe(config, session, memo, **kwargs):
        model = kwargs.get("model_name") or config.whisper.initial_model
        transcribed_calls.append((memo.file_hash, model))
        t = Transcription(
            memo_hash=memo.file_hash,
            transcribed_at=datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            model_name=model,
            text=f"[00:00] Transcribed with {model}",
        )
        session.add(t)
        session.flush()
        md = text_path(config.data_dir, memo.file_id)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(f"Transcribed with {model}\n")

    with patch("easytrans.app.transcribe_memo", side_effect=mock_transcribe):
        async with app.run_test() as pilot:
            await pilot.pause(delay=3.0)

    # Should only have default-model upgrade, no tier 1
    assert transcribed_calls == [("hash1", "small")]


@pytest.mark.asyncio
async def test_default_model_upgrade_skips_already_upgraded(tmp_path: Path) -> None:
    """Memos that already have default-model transcription are not upgraded again."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, "hash1", "2026-0001", text="Done")
    with Session(app.engine) as session:
        t1 = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Done",
        )
        t2 = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            model_name="small",
            text="[00:00] Done upgraded",
        )
        session.add_all([t1, t2])
        session.commit()

    with patch("easytrans.app.transcribe_memo") as mock_tm:
        async with app.run_test() as pilot:
            await pilot.pause(delay=2.0)

    mock_tm.assert_not_called()


@pytest.mark.asyncio
async def test_default_model_upgrade_skipped_when_same_as_initial(tmp_path: Path) -> None:
    """Default-model upgrade is skipped when default_model == initial_model."""
    app = _make_app(tmp_path)
    # Override config so default_model equals initial_model
    app.config.whisper.default_model = "tiny"

    _add_memo(app.engine, tmp_path, "hash1", "2026-0001", text="Done")
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash="hash1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] Done",
        )
        session.add(t)
        session.commit()

    with patch("easytrans.app.transcribe_memo") as mock_tm:
        async with app.run_test() as pilot:
            await pilot.pause(delay=2.0)

    mock_tm.assert_not_called()


# --- Playback tests ---


def _add_memo_with_audio_and_segments(
    app: EasyTransApp,
    tmp_path: Path,
    file_hash: str = "phash1",
    file_id: str = "2026-0010",
    segments_text: str = "[00:00] First line\n[00:05] Second line\n[00:10] Third line",
) -> None:
    """Add a memo, fake audio source file, .md file, and timestamped DB transcription."""
    _add_memo(app.engine, tmp_path, file_hash=file_hash, file_id=file_id, text="clean text")

    # Create a fake source audio file so find_source_audio() succeeds.
    year = file_id.split("-")[0]
    audio_dir = tmp_path / "data" / "audio" / year
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / f"{file_id}.mp3").write_bytes(b"")

    # Add a transcription with the timestamped segments.
    with Session(app.engine) as session:
        t = Transcription(
            memo_hash=file_hash,
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text=segments_text,
        )
        session.add(t)
        session.commit()


def _install_stub_player(app: EasyTransApp) -> list[StubAudioPlayer]:
    """Patch _make_audio_player to record and return stub players."""
    created: list[StubAudioPlayer] = []

    def factory():
        p = StubAudioPlayer(duration=60.0)
        created.append(p)
        return p

    app._make_audio_player = factory  # type: ignore[method-assign]
    return created


@pytest.mark.asyncio
async def test_play_starts_playback(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert len(players) == 1
        assert players[0].is_playing is True
        assert app._is_playing is True
        assert app.show_timestamps is True
        assert len(app._playback_segments) == 3
        assert app._playback_segment_idx == 0
        status = app.query_one("#playback-status", PlaybackStatus)
        assert "visible" in status.classes


@pytest.mark.asyncio
async def test_p_toggles_stop(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert app._is_playing is True
        prior_show = app._playback_saved_show_timestamps

        await pilot.press("p")
        await pilot.pause()
        assert app._is_playing is False
        assert players[0].stop_called is True
        assert app.show_timestamps == prior_show
        status = app.query_one("#playback-status", PlaybackStatus)
        assert "visible" not in status.classes


@pytest.mark.asyncio
async def test_seek_forward_and_back(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()

        await pilot.press("greater_than_sign")
        await pilot.pause()
        assert players[0].relative_seeks == [5.0]

        await pilot.press("less_than_sign")
        await pilot.pause()
        assert players[0].relative_seeks == [5.0, -5.0]


@pytest.mark.asyncio
async def test_down_during_playback_advances_segment(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert app._playback_segment_idx == 0

        await pilot.press("down")
        await pilot.pause()
        assert app._playback_segment_idx == 1
        assert players[0].absolute_seeks == [5.0]
        assert players[0].time_pos == 5.0

        await pilot.press("down")
        await pilot.pause()
        assert app._playback_segment_idx == 2
        assert players[0].absolute_seeks == [5.0, 10.0]


@pytest.mark.asyncio
async def test_up_during_playback_goes_back_a_segment(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app._playback_segment_idx == 2

        await pilot.press("up")
        await pilot.pause()
        assert app._playback_segment_idx == 1
        assert players[0].absolute_seeks[-1] == 5.0


@pytest.mark.asyncio
async def test_j_during_playback_stops_and_navigates(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path, file_hash="phash1", file_id="2026-0010")
    _add_memo_with_audio_and_segments(app, tmp_path, file_hash="phash2", file_id="2026-0011")
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        assert app._get_selected_row_key() == "phash1"
        await pilot.press("p")
        await pilot.pause()
        assert app._is_playing is True

        await pilot.press("j")
        await pilot.pause()
        assert app._is_playing is False
        assert players[0].stop_called is True
        assert app._get_selected_row_key() == "phash2"


@pytest.mark.asyncio
async def test_k_during_playback_stops_and_navigates(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path, file_hash="phash1", file_id="2026-0010")
    _add_memo_with_audio_and_segments(app, tmp_path, file_hash="phash2", file_id="2026-0011")
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.pause()
        assert app._get_selected_row_key() == "phash2"

        await pilot.press("p")
        await pilot.pause()
        assert app._is_playing is True

        await pilot.press("k")
        await pilot.pause()
        assert app._is_playing is False
        assert players[0].stop_called is True
        assert app._get_selected_row_key() == "phash1"


@pytest.mark.asyncio
async def test_up_outside_playback_navigates_table(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path, file_hash="phash1", file_id="2026-0010")
    _add_memo_with_audio_and_segments(app, tmp_path, file_hash="phash2", file_id="2026-0011")

    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.pause()
        assert app._get_selected_row_key() == "phash2"
        await pilot.press("up")
        await pilot.pause()
        assert app._get_selected_row_key() == "phash1"


@pytest.mark.asyncio
async def test_tick_updates_segment_highlight(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert app._playback_segment_idx == 0

        players[0].time_pos = 7.0
        app._on_playback_tick()
        await pilot.pause()
        assert app._playback_segment_idx == 1

        players[0].time_pos = 12.0
        app._on_playback_tick()
        await pilot.pause()
        assert app._playback_segment_idx == 2


@pytest.mark.asyncio
async def test_tick_natural_end_stops_playback(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()

        players[0].time_pos = None
        app._on_playback_tick()
        await pilot.pause()
        assert app._is_playing is False


@pytest.mark.asyncio
async def test_play_with_no_transcription(tmp_path: Path) -> None:
    """Playback still works for a memo with no DB transcription (no highlight)."""
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, file_hash="phash1", file_id="2026-0010", text="clean")
    audio_dir = tmp_path / "data" / "audio" / "2026"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "2026-0010.mp3").write_bytes(b"")
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert app._is_playing is True
        assert app._playback_segments == []
        assert app.show_timestamps is False
        assert players[0].is_playing is True


@pytest.mark.asyncio
async def test_quit_stops_playback(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert players[0].is_playing is True
        app.action_quit()
        await pilot.pause()
        assert players[0].stop_called is True


@pytest.mark.asyncio
async def test_play_no_audio_file_warns(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo(app.engine, tmp_path, file_hash="phash1", file_id="2026-0010")
    with Session(app.engine) as session:
        session.add(Transcription(
            memo_hash="phash1",
            transcribed_at=datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc),
            model_name="tiny",
            text="[00:00] hi",
        ))
        session.commit()
    players = _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert app._is_playing is False
        assert players == []


@pytest.mark.asyncio
async def test_highlight_renders_in_preview(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _add_memo_with_audio_and_segments(app, tmp_path)
    _install_stub_player(app)

    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        preview_text = app.query_one("#preview-text", Static)
        from rich.text import Text as RichText
        content = preview_text.content
        assert isinstance(content, RichText)
        assert "First line" in content.plain
        assert "Second line" in content.plain
        assert "Third line" in content.plain
        styles = [str(span.style) for span in content.spans]
        assert any("reverse" in s for s in styles)
