"""Textual TUI application for EasyTrans."""

import os
import subprocess
import threading
from pathlib import Path

from rich.style import Style as RichStyle
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.events import Key
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, Static

from easytrans.config import EasyTransConfig, load_config
from easytrans.db import get_engine, get_memos, get_latest_transcription
from easytrans.files import find_source_audio, text_path
from easytrans.models import Memo
from easytrans.sync import run_sync, scan_recorder
from easytrans.transcribe import transcribe_memo

# Status indicators
CIRCLE_OPEN = "\u25cb"    # ○
CIRCLE_FILLED = "\u25cf"  # ●

# Column render widths (content_width + 2 * cell_padding where padding=1)
# Status: content=1, render=3
# ID: content=9, render=11
# Length: content=6, render=8
# Model: content=6, render=8
_FIXED_RENDER_W = 3 + 11 + 8 + 8   # = 30
# Recorded: content=16, render=18
# Transcribed: content=16, render=18
_DATES_RENDER_W = 18 + 18           # = 36
_CELL_PAD_RENDER = 2                # 2 * cell_padding for preview column


class MemoPreview(Static):
    """Preview pane showing the selected memo's transcription text."""

    DEFAULT_CSS = """
    MemoPreview {
        height: 1fr;
        padding: 1 2;
        overflow-y: auto;
        border-top: solid $accent;
    }
    """


class GotoStatus(Static):
    """Status bar showing the goto buffer content."""

    DEFAULT_CSS = """
    GotoStatus {
        height: 1;
        background: $accent;
        color: $text;
        display: none;
        padding: 0 1;
    }
    GotoStatus.visible {
        display: block;
    }
    """


class MemoTable(DataTable):
    """DataTable subclass with vim navigation and per-row highlighting."""

    class GotoStatusChanged(Message):
        """Posted when the goto buffer changes. Empty string = hide."""
        def __init__(self, display: str) -> None:
            super().__init__()
            self.display = display

    class NavigateToItem(Message):
        """Request navigation to a specific item by file_id components."""
        def __init__(self, year: int, seq: int, raw_input: str) -> None:
            super().__init__()
            self.year = year
            self.seq = seq
            self.raw_input = raw_input

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("G", "jump_to_last", "Last", show=False),
        Binding("ctrl+d", "scroll_half_page_down", "Half Page Down", show=False),
        Binding("ctrl+u", "scroll_half_page_up", "Half Page Up", show=False),
        Binding("ctrl+f, pagedown", "scroll_page_down", "Page Down", show=False),
        Binding("ctrl+b, pageup", "scroll_page_up", "Page Up", show=False),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed_rows: set[str] = set()
        self._g_pending: bool = False
        self._goto_buffer: str = ""
        self._goto_active: bool = False
        self._goto_suspended: bool = False
        self._skip_auto_scroll: bool = False

    def on_resize(self, event) -> None:
        """Rebuild table layout when the table widget itself is resized."""
        app = self.app
        if hasattr(app, "_refresh_table"):
            app._refresh_table()

    def _render_cell(self, row_index, column_index, base_style, width,
                     cursor=False, hover=False):
        if row_index >= 0:
            try:
                row_key = self._row_locations.get_key(row_index)
                if row_key.value in self.completed_rows:
                    base_style += RichStyle(bgcolor="dark_red")
            except (KeyError, IndexError):
                pass
        return super()._render_cell(
            row_index, column_index, base_style, width, cursor, hover,
        )

    # --- Auto-scroll suppression ---

    def _scroll_cursor_into_view(self, animate: bool = False) -> None:
        """Override to suppress auto-scroll during vim viewport panning."""
        if self._skip_auto_scroll:
            return
        super()._scroll_cursor_into_view(animate=animate)

    # --- Key handling ---

    def on_key(self, event: Key) -> None:
        # gg handling — must come first
        if event.character == "g":
            if self._g_pending:
                self._g_pending = False
                self.action_jump_to_first()
            else:
                self._g_pending = True
                self._clear_goto_buffer()
            event.prevent_default()
            event.stop()
            return

        # Any non-g key clears the g-pending state
        self._g_pending = False

        if self._goto_suspended:
            return

        # Digit and separator accumulation
        if event.character and (event.character.isdigit() or event.character == "-"):
            # Only allow one separator, and don't start with "-"
            if event.character == "-" and ("-" in self._goto_buffer or not self._goto_buffer):
                return
            self._goto_buffer += event.character
            self._goto_active = True
            self.post_message(self.GotoStatusChanged(f"Go to: {self._goto_buffer}_"))
            event.prevent_default()
            event.stop()
            return

        # Backspace removes last character from goto buffer
        if event.key == "backspace" and self._goto_active:
            if self._goto_buffer:
                self._goto_buffer = self._goto_buffer[:-1]
            self.post_message(self.GotoStatusChanged(f"Go to: {self._goto_buffer}_"))
            event.prevent_default()
            event.stop()
            return

        # Enter → execute goto or exit goto mode
        if event.key == "enter" and self._goto_active:
            if self._goto_buffer:
                self._execute_goto()
            else:
                self._clear_goto_buffer()
            event.prevent_default()
            event.stop()
            return

        # Count + j/k
        if self._goto_buffer and event.character in ("j", "k"):
            try:
                count = int(self._goto_buffer)
            except ValueError:
                count = 0
            if count > 0:
                for _ in range(count):
                    if event.character == "j":
                        self.action_cursor_down()
                    else:
                        self.action_cursor_up()
            self._clear_goto_buffer()
            event.prevent_default()
            event.stop()
            return

        # Escape clears buffer
        if event.key == "escape" and self._goto_active:
            self._clear_goto_buffer()
            event.prevent_default()
            event.stop()
            return

        # Any other key clears
        if self._goto_active:
            self._clear_goto_buffer()

    def _clear_goto_buffer(self) -> None:
        self._goto_buffer = ""
        self._goto_active = False
        self.post_message(self.GotoStatusChanged(""))

    def _execute_goto(self) -> None:
        raw_input = self._goto_buffer
        buffer = raw_input.strip("-")
        self._clear_goto_buffer()
        if not buffer:
            return

        if "-" in buffer:
            # Two-part: year-seq
            parts = buffer.split("-", 1)
            try:
                year = int(parts[0])
                seq = int(parts[1]) if parts[1] else 1
            except ValueError:
                return
            self.post_message(self.NavigateToItem(year, seq, raw_input))
        else:
            # Single-part: just a sequence number (year=0 means any year)
            try:
                seq = int(buffer)
            except ValueError:
                return
            self.post_message(self.NavigateToItem(0, seq, raw_input))

    # --- Navigation actions ---

    def action_jump_to_first(self) -> None:
        if self.row_count > 0:
            self.move_cursor(row=0)

    def action_jump_to_last(self) -> None:
        if self.row_count > 0:
            self.move_cursor(row=self.row_count - 1)

    # --- Viewport scrolling helpers ---

    def _row_y(self, row_index: int) -> int:
        """Scroll-relative y position of a row.

        _get_row_region returns y in absolute coords (including the fixed
        header height).  Subtracting row 0's y cancels the header offset
        so the result is usable as a scroll_y value.
        """
        _, y, _, _ = self._get_row_region(row_index)
        _, base_y, _, _ = self._get_row_region(0)
        return y - base_y

    def _get_row_height(self) -> int:
        """Height of a single row in content lines.

        Uses DataTable's internal _get_row_region for accuracy, since rows
        may render at >1 line (e.g. wrapped text).
        """
        if self.row_count > 0:
            _, _, _, height = self._get_row_region(0)
            return max(1, height)
        return 1

    def _get_viewport_height(self) -> int:
        """Usable viewport height in lines, excluding the fixed header."""
        h = self.scrollable_content_region.height - self._get_fixed_offset().top
        return max(1, h)

    def _get_visible_row_count(self) -> int:
        """Number of rows that fit in the viewport."""
        h = self._get_viewport_height()
        return max(1, h // self._get_row_height())

    def _get_first_visible_row(self) -> int:
        """Index of the first fully visible row."""
        if self.row_count == 0:
            return 0
        sy = int(self.scroll_y)
        for i in range(self.row_count):
            row_top = self._row_y(i)
            _, _, _, h = self._get_row_region(i)
            if row_top + h > sy:
                return i
        return self.row_count - 1

    def _get_last_fully_visible_row(self) -> int:
        """Index of the last row whose bottom edge is within the viewport."""
        if self.row_count == 0:
            return 0
        sy = int(self.scroll_y)
        viewport_bottom = sy + self._get_viewport_height()
        last_full = self._get_first_visible_row()
        for i in range(last_full, self.row_count):
            row_bottom = self._row_y(i) + self._get_row_region(i)[3]
            if row_bottom > viewport_bottom:
                break
            last_full = i
        return last_full

    def _get_cursor_screen_offset(self) -> int:
        """Cursor position relative to viewport top (in rows, not lines)."""
        return self.cursor_coordinate.row - self._get_first_visible_row()

    def _scroll_to_row_at_top(self, row: int) -> None:
        """Scroll so `row` is at the top of the viewport."""
        if self.row_count == 0:
            return
        row = max(0, min(row, self.row_count - 1))
        self.scroll_y = float(self._row_y(row))
        # scroll_y may be clamped to max_scroll_y by Textual, which can
        # land mid-row.  Snap to the actual first fully-visible row so
        # no partial row peeks above the viewport.
        self.scroll_y = float(self._row_y(self._get_first_visible_row()))

    def _scroll_and_move_cursor(self, new_row: int, new_first_visible: int) -> None:
        """Scroll to position and update cursor without flicker."""
        self._scroll_to_row_at_top(new_first_visible)
        self._skip_auto_scroll = True
        self.move_cursor(row=new_row)
        # Defer reset so deferred _scroll_cursor_into_view calls are suppressed
        self.call_after_refresh(self._reset_skip_auto_scroll)

    def _reset_skip_auto_scroll(self) -> None:
        self._skip_auto_scroll = False

    # --- Half-page scroll ---

    def action_scroll_half_page_down(self) -> None:
        if self.row_count == 0:
            return
        visible = self._get_visible_row_count()
        half = max(1, visible // 2)
        offset = self._get_cursor_screen_offset()
        first = self._get_first_visible_row()

        max_first = max(0, self.row_count - visible)
        new_first = min(first + half, max_first)

        if new_first == first:
            # At bottom scroll limit — move cursor instead
            self.move_cursor(row=min(self.cursor_coordinate.row + half, self.row_count - 1))
            return

        new_row = max(0, min(new_first + offset, self.row_count - 1))
        self._scroll_and_move_cursor(new_row, new_first)

    def action_scroll_half_page_up(self) -> None:
        if self.row_count == 0:
            return
        visible = self._get_visible_row_count()
        half = max(1, visible // 2)
        offset = self._get_cursor_screen_offset()
        first = self._get_first_visible_row()

        new_first = max(0, first - half)

        if new_first == first:
            # At top scroll limit — move cursor instead
            self.move_cursor(row=max(self.cursor_coordinate.row - half, 0))
            return

        new_row = max(0, min(new_first + offset, self.row_count - 1))
        self._scroll_and_move_cursor(new_row, new_first)

    # --- Full-page scroll ---

    def action_scroll_page_down(self) -> None:
        if self.row_count == 0:
            return
        visible = self._get_visible_row_count()
        scroll_amount = max(1, visible - 2)
        first = self._get_first_visible_row()

        max_first = max(0, self.row_count - visible)
        new_first = min(first + scroll_amount, max_first)
        new_row = max(0, min(new_first, self.row_count - 1))
        self._scroll_and_move_cursor(new_row, new_first)

    def action_scroll_page_up(self) -> None:
        if self.row_count == 0:
            return
        visible = self._get_visible_row_count()
        scroll_amount = max(1, visible - 2)
        first = self._get_first_visible_row()

        new_first = max(0, first - scroll_amount)
        # Scroll first, then find the actual last fully visible row
        # so the cursor never lands on a partially-clipped bottom row.
        self._scroll_to_row_at_top(new_first)
        new_row = self._get_last_fully_visible_row()
        self._skip_auto_scroll = True
        self.move_cursor(row=new_row)
        self.call_after_refresh(self._reset_skip_auto_scroll)

    # --- Goto suspension ---

    def suspend_goto(self) -> None:
        self._goto_suspended = True
        self._clear_goto_buffer()

    def resume_goto(self) -> None:
        self._goto_suspended = False


class EasyTransApp(App):
    """Main EasyTrans application."""

    TITLE = "EasyTrans"
    CSS = """
    #memo-table {
        height: 2fr;
    }
    #preview-area {
        height: 1fr;
    }
    #preview {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("s", "sync", "Sync"),
        Binding("h", "toggle_completed", "Hide/Show done"),
        Binding("e", "edit", "Edit"),
        Binding("r", "retranscribe", "Re-transcribe"),
        Binding("p", "play", "Play"),
        Binding("t", "toggle_timestamps", "Timestamps"),
        Binding("c", "copy_text", "Copy"),
        Binding("shift+c", "copy_timestamps", "Copy+timestamps"),
        Binding("d", "mark_complete", "Done"),
        Binding("q", "quit", "Quit"),
    ]

    show_completed: bool = False
    show_timestamps: bool = False

    def __init__(self, config: EasyTransConfig | None = None) -> None:
        super().__init__()
        self.config = config or load_config()
        self.config.ensure_dirs()
        self.engine: Engine = get_engine(self.config.db_path)
        self._retranscribe_worker = None
        # Memos marked done this session — shown with red bg until restart
        self._session_completed: set[str] = set()
        # Whether date columns are currently visible
        self._show_date_columns: bool = True
        # Child processes running Whisper; killed on quit
        self._active_processes: set = set()
        # Event signalling that the app is shutting down; checked by workers
        self._shutting_down = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield MemoTable(id="memo-table")
            with Vertical(id="preview-area"):
                yield MemoPreview(id="preview")
                yield GotoStatus(id="goto-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#memo-table", MemoTable)
        table.cursor_type = "row"
        table.completed_rows = self._session_completed
        self._refresh_table()

    def _get_selected_row_key(self) -> str | None:
        """Get the row key value for the currently selected row."""
        table = self.query_one("#memo-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        return row_key.value if row_key else None

    def _move_cursor_to_key(self, key_value: str | None) -> None:
        """Move the table cursor to the row with the given key, if it exists."""
        if key_value is None:
            return
        table = self.query_one("#memo-table", DataTable)
        for row_idx in range(table.row_count):
            rk, _ = table.coordinate_to_cell_key(Coordinate(row_idx, 0))
            if rk.value == key_value:
                table.move_cursor(row=row_idx)
                return

    def _refresh_table(self) -> None:
        """Reload memo list from database, preserving cursor position."""
        table = self.query_one("#memo-table", DataTable)
        saved_key = self._get_selected_row_key()

        # Calculate available width for columns
        # Use the table's own width (more reliable during resize)
        available = table.size.width or self.size.width
        if available <= 0:
            available = 120
        available -= 2  # vertical scrollbar gutter

        # Decide whether to show date columns based on Preview space
        preview_w_with_dates = available - _FIXED_RENDER_W - _DATES_RENDER_W - _CELL_PAD_RENDER
        self._show_date_columns = preview_w_with_dates >= 20

        if self._show_date_columns:
            preview_content_w = preview_w_with_dates
        else:
            preview_content_w = available - _FIXED_RENDER_W - _CELL_PAD_RENDER
        preview_content_w = max(preview_content_w, 10)

        # Rebuild columns
        table.clear(columns=True)
        table.add_column("", width=1)
        table.add_column("ID", width=9)
        table.add_column("Length", width=6)
        table.add_column("Model", width=6)
        table.add_column("Preview", width=preview_content_w)
        if self._show_date_columns:
            table.add_column("Recorded", width=16)
            table.add_column("Transcribed", width=16)

        with Session(self.engine) as session:
            # Include completed memos if toggled, plus any completed this session
            memos = get_memos(session, include_completed=self.show_completed)
            shown_hashes = {m.file_hash for m in memos}

            # Add session-completed memos that aren't already in the list
            if not self.show_completed:
                for h in self._session_completed:
                    if h not in shown_hashes:
                        memo = session.get(Memo, h)
                        if memo:
                            memos.append(memo)
                # Re-sort by file_id
                memos.sort(key=lambda m: m.file_id)

            for memo in memos:
                status = CIRCLE_FILLED if memo.completed else CIRCLE_OPEN
                recorded = memo.recorded_at.strftime("%Y-%m-%d %H:%M")
                # Format duration as MM:SS
                if memo.duration_seconds is not None:
                    mins = int(memo.duration_seconds) // 60
                    secs = int(memo.duration_seconds) % 60
                    length = f"{mins}:{secs:02d}"
                else:
                    length = ""
                # Get latest transcription info
                latest = get_latest_transcription(session, memo.file_hash)
                model = latest.model_name if latest else ""
                transcribed = (
                    latest.transcribed_at.strftime("%Y-%m-%d %H:%M")
                    if latest else ""
                )
                # Read first line from .md file for preview
                md = text_path(self.config.data_dir, memo.file_id)
                preview = ""
                if md.exists():
                    text = md.read_text().strip()
                    first_line = text.split("\n")[0] if text else ""
                    # Show at least 60 chars (wrapping if needed), but
                    # if the column is wider than 60, fill without wrapping.
                    max_chars = max(60, preview_content_w)
                    preview = first_line[:max_chars]
                cells = [status, memo.file_id, length, model, preview]
                if self._show_date_columns:
                    cells.extend([recorded, transcribed])
                table.add_row(*cells, key=memo.file_hash, height=None)

        self._move_cursor_to_key(saved_key)
        self._update_preview()

    def _get_selected_memo(self) -> Memo | None:
        """Get the currently selected memo from the table."""
        key_value = self._get_selected_row_key()
        if key_value is None:
            return None
        with Session(self.engine) as session:
            memo = session.get(Memo, key_value)
            if memo:
                session.expunge(memo)
            return memo

    def _update_preview(self) -> None:
        """Update the preview pane with the selected memo's text."""
        preview = self.query_one("#preview", MemoPreview)
        memo = self._get_selected_memo()
        if memo is None:
            preview.update("No memo selected")
            return

        parts = []

        # Show dates in preview when date columns are hidden
        if not self._show_date_columns:
            recorded = memo.recorded_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"Recorded: {recorded}")
            with Session(self.engine) as session:
                t = get_latest_transcription(session, memo.file_hash)
                if t:
                    transcribed = t.transcribed_at.strftime("%Y-%m-%d %H:%M")
                    parts.append(f"Transcribed: {transcribed}")
            parts.append("")

        if self.show_timestamps:
            with Session(self.engine) as session:
                t = get_latest_transcription(session, memo.file_hash)
                if t:
                    parts.append(t.text)
                else:
                    parts.append("(not yet transcribed)")
        else:
            md = text_path(self.config.data_dir, memo.file_id)
            if md.exists():
                text = md.read_text()
                parts.append(text if text.strip() else "(empty transcription)")
            else:
                parts.append("(not yet transcribed)")

        preview.update("\n".join(parts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_preview()

    def on_memo_table_goto_status_changed(self, event: MemoTable.GotoStatusChanged) -> None:
        """Show/hide goto buffer status in the footer area."""
        status = self.query_one("#goto-status", GotoStatus)
        if event.display:
            status.update(event.display)
            status.add_class("visible")
        else:
            status.update("")
            status.remove_class("visible")

    def on_memo_table_navigate_to_item(self, event: MemoTable.NavigateToItem) -> None:
        """Navigate to a memo by file_id components."""
        table = self.query_one("#memo-table", MemoTable)
        if event.year == 0:
            target_suffix = f"-{event.seq:04d}"
            target_id = None  # unknown full ID for single-part
        else:
            target_suffix = None
            target_id = f"{event.year}-{event.seq:04d}"

        # Search visible rows
        for row_idx in range(table.row_count):
            rk, _ = table.coordinate_to_cell_key(Coordinate(row_idx, 0))
            file_id = self._get_file_id_for_key(rk.value)
            if file_id and (
                (target_id and file_id == target_id)
                or (target_suffix and file_id.endswith(target_suffix))
            ):
                table.move_cursor(row=row_idx)
                return

        # Not in the visible list — check if it exists but is hidden (completed)
        display_id = target_id or f"*-{event.seq:04d}"
        with Session(self.engine) as session:
            from sqlalchemy import select
            query = select(Memo)
            if target_id:
                query = query.where(Memo.file_id == target_id)
            else:
                query = query.where(Memo.file_id.like(f"%-{event.seq:04d}"))
            memo = session.execute(query).scalars().first()
            if memo and memo.completed and not self.show_completed:
                self.notify(
                    f"{memo.file_id} is marked done (press h to show)",
                    severity="warning",
                )
            else:
                self.notify(
                    f"No memo matching '{event.raw_input}'",
                    severity="warning",
                )

    def _get_file_id_for_key(self, key_value: str) -> str | None:
        """Look up a memo's file_id from its row key (file_hash)."""
        with Session(self.engine) as session:
            memo = session.get(Memo, key_value)
            return memo.file_id if memo else None

    def action_toggle_completed(self) -> None:
        self.show_completed = not self.show_completed
        label = "showing" if self.show_completed else "hiding"
        self.notify(f"Completed memos: {label}")
        self._refresh_table()

    def action_mark_complete(self) -> None:
        memo = self._get_selected_memo()
        if memo is None:
            self.notify("No memo selected", severity="warning")
            return
        marked_complete = False
        with Session(self.engine) as session:
            db_memo = session.get(Memo, memo.file_hash)
            if db_memo:
                db_memo.completed = not db_memo.completed
                session.commit()
                if db_memo.completed:
                    marked_complete = True
                    self._session_completed.add(memo.file_hash)
                    self.notify(f"Marked {memo.file_id} as complete")
                else:
                    self._session_completed.discard(memo.file_hash)
                    self.notify(f"Marked {memo.file_id} as incomplete")
        self._refresh_table()
        # Advance cursor to next row after marking complete
        if marked_complete:
            table = self.query_one("#memo-table", DataTable)
            row = table.cursor_coordinate.row
            if row < table.row_count - 1:
                table.move_cursor(row=row + 1)

    def action_play(self) -> None:
        """Play the selected memo's audio file."""
        memo = self._get_selected_memo()
        if memo is None:
            self.notify("No memo selected", severity="warning")
            return
        src = find_source_audio(self.config.data_dir, memo.file_id)
        if src is None:
            self.notify("Audio file not found", severity="warning")
            return
        # Try common audio players
        for cmd in (["ffplay", "-nodisp", "-autoexit"], ["mpv", "--no-video"], ["aplay"]):
            try:
                with self.suspend():
                    subprocess.run(cmd + [str(src)])
                return
            except FileNotFoundError:
                continue
        self.notify("No audio player found (install ffplay or mpv)", severity="warning")

    def action_toggle_timestamps(self) -> None:
        """Toggle timestamp display in preview pane."""
        self.show_timestamps = not self.show_timestamps
        label = "on" if self.show_timestamps else "off"
        self.notify(f"Timestamps: {label}")
        self._update_preview()

    def _build_front_matter(self, memo: Memo) -> str:
        """Build YAML front matter for a memo."""
        status = "done" if memo.completed else "pending"
        recorded = memo.recorded_at.strftime("%Y-%m-%d %H:%M")
        lines = [
            "---",
            f"id: {memo.file_id}",
            f"status: {status}",
            f"recorded: {recorded}",
        ]
        if memo.duration_seconds is not None:
            mins = int(memo.duration_seconds) // 60
            secs = int(memo.duration_seconds) % 60
            lines.append(f"length: {mins}:{secs:02d}")
        lines.append("---")
        lines.append("")  # blank line between front matter and text
        return "\n".join(lines) + "\n"

    @staticmethod
    def _strip_front_matter(text: str) -> str:
        """Remove YAML front matter and trailing blank line from text."""
        if not text.startswith("---\n"):
            return text
        end = text.find("\n---\n", 4)
        if end == -1:
            return text
        rest = text[end + 5:]
        # Strip the blank line that follows the closing ---
        if rest.startswith("\n"):
            rest = rest[1:]
        return rest

    def action_edit(self) -> None:
        memo = self._get_selected_memo()
        if memo is None:
            self.notify("No memo selected", severity="warning")
            return
        md = text_path(self.config.data_dir, memo.file_id)
        if not md.exists():
            self.notify("No transcription file to edit", severity="warning")
            return

        # Prepend front matter for editing
        original_text = md.read_text()
        front_matter = self._build_front_matter(memo)
        md.write_text(front_matter + original_text)

        editor = os.environ.get("EDITOR", "vi")
        with self.suspend():
            subprocess.run([editor, str(md)])

        # Strip front matter after editing
        edited = md.read_text()
        md.write_text(self._strip_front_matter(edited))
        self._refresh_table()

    def action_copy_text(self) -> None:
        self._copy_to_clipboard(with_timestamps=False)

    def action_copy_timestamps(self) -> None:
        self._copy_to_clipboard(with_timestamps=True)

    def _copy_to_clipboard(self, with_timestamps: bool) -> None:
        memo = self._get_selected_memo()
        if memo is None:
            self.notify("No memo selected", severity="warning")
            return

        if with_timestamps:
            with Session(self.engine) as session:
                t = get_latest_transcription(session, memo.file_hash)
                if t is None:
                    self.notify("No transcription available", severity="warning")
                    return
                text = t.text
        else:
            md = text_path(self.config.data_dir, memo.file_id)
            if not md.exists():
                self.notify("No transcription file", severity="warning")
                return
            text = md.read_text()

        # Try xclip, then xsel, then pbcopy
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"], ["pbcopy"]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True)
                self.notify("Copied to clipboard")
                return
            except FileNotFoundError:
                continue
        self.notify("No clipboard tool found (install xclip)", severity="warning")

    @work(thread=True)
    def action_sync(self) -> None:
        """Sync files from recorder and transcribe new memos."""
        self.notify("Starting sync...")
        with Session(self.engine) as session:
            new_memos = run_sync(self.config, session)
            session.commit()

            if not new_memos:
                self.call_from_thread(self.notify, "No new recordings found")
                self.call_from_thread(self._refresh_table)
                return

            self.call_from_thread(
                self.notify,
                f"Found {len(new_memos)} new recording(s), transcribing...",
            )
            self.call_from_thread(self._refresh_table)

            for memo in new_memos:
                if self._shutting_down.is_set():
                    return
                try:
                    transcribe_memo(
                        self.config, session, memo,
                        active_processes=self._active_processes,
                    )
                    session.commit()
                    self.call_from_thread(
                        self.notify, f"Transcribed {memo.file_id}"
                    )
                except Exception as e:
                    if self._shutting_down.is_set():
                        return
                    self.call_from_thread(
                        self.notify,
                        f"Error transcribing {memo.file_id}: {e}",
                        severity="error",
                    )

            self.call_from_thread(self._refresh_table)
            self.call_from_thread(
                self.notify, "Sync complete!"
            )

    def _update_row_cell(self, row_key_value: str, col_idx: int, value: str) -> None:
        """Update a single cell in the table by row key and column index."""
        table = self.query_one("#memo-table", DataTable)
        for row_idx in range(table.row_count):
            rk, _ = table.coordinate_to_cell_key(Coordinate(row_idx, 0))
            if rk.value == row_key_value:
                table.update_cell_at(Coordinate(row_idx, col_idx), value)
                return

    def action_retranscribe(self) -> None:
        """Re-transcribe the selected memo with the larger model."""
        memo = self._get_selected_memo()
        if memo is None:
            self.notify("No memo selected", severity="warning")
            return

        model = self.config.whisper.large_model
        self.notify(f"Re-transcribing {memo.file_id} with {model}...")

        # Immediately update Model and Preview columns to show progress
        self._update_row_cell(memo.file_hash, 3, model)  # Model column
        self._update_row_cell(memo.file_hash, 4, "(transcribing...)")  # Preview column

        self._retranscribe_worker = self._do_retranscribe(memo, model)

    @work(thread=True, exclusive=True, group="retranscribe")
    def _do_retranscribe(self, memo: Memo, model: str) -> None:
        try:
            if self._shutting_down.is_set():
                return
            with Session(self.engine) as session:
                transcribe_memo(
                    self.config, session, memo,
                    model_name=model, overwrite_md=True,
                    active_processes=self._active_processes,
                )
                session.commit()
            if self._shutting_down.is_set():
                return
            self.call_from_thread(
                self.notify, f"Re-transcribed {memo.file_id}"
            )
            self.call_from_thread(self._refresh_table)
        except Exception as e:
            if self._shutting_down.is_set():
                return
            self.call_from_thread(
                self.notify,
                f"Error: {e}",
                severity="error",
            )
            self.call_from_thread(self._refresh_table)
        finally:
            self._retranscribe_worker = None

    def action_quit(self) -> None:
        """Quit, killing any background transcription processes."""
        self._shutting_down.set()
        for p in list(self._active_processes):
            p.kill()
        self.workers.cancel_all()
        self.exit()
