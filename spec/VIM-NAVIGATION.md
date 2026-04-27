# Vim-Style List Navigation for Textual Apps — Implementation Guide

This document captures the patterns, code, and Textual-specific learnings from implementing vim-style list navigation in a Textual `ListView`, to be used as reference when reimplementing in another app.

## Overview

The navigation system has three layers, all implemented on a class extending Textual's `ListView`:

1. **Basic cursor movement** — j/k/Up/Down/Ctrl+N/Ctrl+P, G, gg
2. **Viewport scrolling** — Ctrl+D/U (half page), Ctrl+F/B/PageDown/PageUp (full page)
3. **Numeric prefix system** — digit accumulation → count+j/k, goto item, goto year-item

---

## 1. Bindings

```python
BINDINGS = [
    Binding("j, ctrl+n", "cursor_down", "Down", show=False),
    Binding("k, ctrl+p", "cursor_up", "Up", show=False),
    # gg is handled by on_key below (double-key sequence can't be a Binding)
    Binding("G", "jump_to_last", "Last", show=False),
    Binding("ctrl+d", "scroll_half_page_down", "Half Page Down", show=False),
    Binding("ctrl+u", "scroll_half_page_up", "Half Page Up", show=False),
    Binding("ctrl+f, pagedown", "scroll_page_down", "Page Down", show=False),
    Binding("ctrl+b, pageup", "scroll_page_up", "Page Up", show=False),
]
```

**Key points:**
- `cursor_down` / `cursor_up` are built-in `ListView` actions — no need to implement them.
- Use comma-separated keys in a single `Binding()`, not multiple `Binding` objects.
- `gg` can't be a binding (it's a two-key sequence). Handle it in `on_key`.
- `show=False` hides these from the footer bar.

## 2. gg (Jump to First) — Double-Key Sequence

State: a single `_g_pending: bool` flag, initialized `False`.

```python
def on_key(self, event: Key) -> None:
    # gg handling — must come first, before goto buffer logic
    if event.character == "g":
        if self._g_pending:
            self._g_pending = False
            self.action_jump_to_first()
        else:
            self._g_pending = True
            self._clear_goto_buffer()  # typing 'g' cancels any digit accumulation
        event.prevent_default()
        event.stop()
        return

    # Any non-g key clears the g-pending state
    self._g_pending = False

    # ... rest of on_key (goto buffer, etc.)
```

**Important:** The `g` handler clears the goto buffer. This prevents the ambiguity where a user types "5g" — the 5 is discarded, and a single `g` is registered as the first press.

## 3. G (Jump to Last)

Simple action:

```python
def action_jump_to_first(self) -> None:
    if self.children:
        self.index = 0

def action_jump_to_last(self) -> None:
    if self.children:
        self.index = len(self.children) - 1
```

Setting `self.index` on a `ListView` automatically updates the highlight and scrolls to show the item (via `watch_index`).

## 4. Viewport Scrolling (Ctrl+D/U/F/B)

### Key Concept: Suppress Auto-Scroll

When you set `self.index`, Textual's `ListView` automatically scrolls to show the highlighted item. For vim-style scrolling, you need to control scroll position independently. The pattern:

```python
# Instance variable
self._skip_auto_scroll: bool = False
```

Override `watch_index` to check this flag:

```python
def watch_index(self, old_index: int | None, new_index: int | None) -> None:
    # ... update highlighting (always) ...
    if new_index is not None and 0 <= new_index < len(self._nodes):
        new_child = self._nodes[new_index]
        new_child.highlighted = True
        # Only auto-scroll if not suppressed
        if not self._skip_auto_scroll:
            if new_child.region:
                self.scroll_to_widget(new_child, animate=False)
            else:
                self.call_after_refresh(self.scroll_to_widget, new_child, animate=False)
```

### Helper Methods

```python
def _get_item_height(self) -> int:
    """Height of a single item (assumes uniform height)."""
    if self.children:
        return max(1, self.children[0].region.height)
    return 1

def _get_visible_item_count(self) -> int:
    """Number of items that fit in the viewport."""
    region_height = self.scrollable_content_region.height
    if region_height <= 0:
        return 10  # fallback
    return max(1, region_height // self._get_item_height())

def _get_first_visible_index(self) -> int:
    """Index of the first visible item."""
    if not self.children:
        return 0
    item_height = self._get_item_height()
    if item_height <= 0:
        return 0
    return max(0, int(self.scroll_y) // item_height)

def _get_cursor_screen_offset(self) -> int:
    """Cursor's position relative to viewport top (0 = top of viewport)."""
    return (self.index or 0) - self._get_first_visible_index()

def _scroll_to_index_at_top(self, index: int) -> None:
    """Scroll so `index` is at the top of the viewport."""
    item_height = self._get_item_height()
    visible_count = self._get_visible_item_count()
    max_first_index = max(0, len(self.children) - visible_count)
    target_first = max(0, min(index, max_first_index))
    target_y = float(target_first * item_height)
    # Set scroll_y directly (not scroll_to) for same-frame batching
    self.scroll_x, self.scroll_y = self.scroll_x, target_y

def _scroll_and_update_index(self, new_index: int, new_first_visible: int) -> None:
    """Scroll to position and update cursor without flicker."""
    self._scroll_to_index_at_top(new_first_visible)
    self._skip_auto_scroll = True
    self.index = new_index
    self._skip_auto_scroll = False
```

**Critical Textual detail:** Use direct `self.scroll_y = ...` assignment, not `self.scroll_to()`. The latter may have different timing that causes flicker when combined with index changes in the same frame.

### Half-Page Scroll (Ctrl+D / Ctrl+U)

Behavior: Pans viewport by half screen. Cursor stays at same screen position. If at scroll limit, moves cursor instead.

```python
def action_scroll_half_page_down(self) -> None:
    if not self.children:
        return
    visible_count = self._get_visible_item_count()
    half_page = max(1, visible_count // 2)
    cursor_screen_offset = self._get_cursor_screen_offset()
    first_visible = self._get_first_visible_index()

    max_first_index = max(0, len(self.children) - visible_count)
    new_first_visible = min(first_visible + half_page, max_first_index)

    # At bottom scroll limit → move cursor instead
    if new_first_visible == first_visible:
        self.index = min((self.index or 0) + half_page, len(self.children) - 1)
        return

    # Cursor stays at same screen position
    new_index = max(0, min(new_first_visible + cursor_screen_offset, len(self.children) - 1))
    self._scroll_and_update_index(new_index, new_first_visible)
```

`action_scroll_half_page_up` is the mirror image (subtract instead of add, check top limit).

### Full-Page Scroll (Ctrl+F / Ctrl+B)

Behavior: Scrolls by `visible_count - 2` (2 lines of overlap). Cursor moves to top (page down) or bottom (page up) of viewport.

```python
def action_scroll_page_down(self) -> None:
    if not self.children:
        return
    visible_count = self._get_visible_item_count()
    scroll_amount = max(1, visible_count - 2)  # 2 lines overlap
    first_visible = self._get_first_visible_index()

    max_first_index = max(0, len(self.children) - visible_count)
    new_first_visible = min(first_visible + scroll_amount, max_first_index)
    new_index = max(0, min(new_first_visible, len(self.children) - 1))  # cursor to top
    self._scroll_and_update_index(new_index, new_first_visible)

def action_scroll_page_up(self) -> None:
    if not self.children:
        return
    visible_count = self._get_visible_item_count()
    scroll_amount = max(1, visible_count - 2)
    first_visible = self._get_first_visible_index()

    new_first_visible = max(0, first_visible - scroll_amount)
    new_index = max(0, min(new_first_visible + visible_count - 1, len(self.children) - 1))  # cursor to bottom
    self._scroll_and_update_index(new_index, new_first_visible)
```

## 5. Numeric Prefix / Goto System

### State

```python
self._goto_buffer: str = ""      # accumulated digits (and possibly a separator)
self._goto_suspended: bool = False  # temporarily disable (e.g. during editing)
```

### Messages

```python
class GotoStatusChanged(Message):
    """Update the status line display. Empty string = hide."""
    def __init__(self, display: str) -> None:
        super().__init__()
        self.display = display

class NavigateToItem(Message):
    """Request navigation to a specific item by ID components."""
    def __init__(self, year: int, seq: int) -> None:
        super().__init__()
        self.year = year
        self.seq = seq
```

### on_key Logic (in full, with gg integrated)

The goto buffer handles three cases based on what's accumulated when the user presses a terminating key:

1. **Digits + j/k** → count movement (move cursor N times)
2. **Digits + Enter** → goto item by sequence number (single-part) or by year-seq (two-part with `-` separator)
3. **Digits + `-` + Digits** → accumulate the full `year-seq` ID
4. **Any other key** → clear the buffer

```python
def on_key(self, event: Key) -> None:
    # gg handling (always first)
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

    self._g_pending = False

    if self._goto_suspended:
        return

    # Digit and separator accumulation
    if event.character and (event.character.isdigit() or event.character == "-"):
        # Only allow one separator
        if event.character == "-" and "-" in self._goto_buffer:
            return
        self._goto_buffer += event.character
        self.post_message(self.GotoStatusChanged(f"Go to: {self._goto_buffer}_"))
        event.prevent_default()
        event.stop()
        return

    # Enter with buffer → execute goto
    if event.key == "enter" and self._goto_buffer:
        self._execute_goto()
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

    # Any other key clears
    if self._goto_buffer:
        self._clear_goto_buffer()
```

### _execute_goto — Handling the Separator

In the original app, `:` separates page:item. In the new app, `-` separates year-seq.

The detection logic:

```python
def _execute_goto(self) -> None:
    buffer = self._goto_buffer.strip("-")  # handle trailing separator
    self._clear_goto_buffer()
    if not buffer:
        return

    if "-" in buffer:
        # Two-part: year-seq → navigate to specific item
        parts = buffer.split("-", 1)
        try:
            year = int(parts[0])
            seq = int(parts[1]) if parts[1] else 1
        except ValueError:
            return
        self.post_message(self.NavigateToItem(year, seq))
    else:
        # Single-part: just the sequence number
        # The parent handles resolving which item this is
        # (unique match in current view → go there;
        #  multiple matches → go to first in list)
        try:
            seq = int(buffer)
        except ValueError:
            return
        self._navigate_to_seq(seq)
```

**Important design note for the `count+j/k` vs `goto` ambiguity:** When the buffer contains only digits (no separator), it's ambiguous — it could be a count for j/k or a sequence number for Enter. The system resolves this by **waiting for the terminating key**: `j`/`k` means count, `Enter` means goto. If the buffer contains a `-`, it can't be a count, so only `Enter` is valid (j/k would just clear the buffer since `int()` would raise `ValueError` on "2025-3").

### Status Line Display

Post a `GotoStatusChanged` message whenever the buffer changes. The parent app listens and shows/hides a status indicator:

- While accumulating: `"Go to: 42_"` (underscore = cursor)
- On clear: `""` (empty = hide)

### Suspension

During certain operations (editing, modal dialogs), you may want to disable the goto buffer so digit keys pass through:

```python
def suspend_goto(self) -> None:
    self._goto_suspended = True
    self._clear_goto_buffer()

def resume_goto(self) -> None:
    self._goto_suspended = False
```

Note: `gg` has its own separate suspension flag (`_gg_suspended`) since it may need to be disabled independently (e.g., when another feature uses the 'g' key).

## 6. Textual-Specific Gotchas

1. **`watch_index` override is essential.** Without it, you can't suppress auto-scroll during vim-style viewport panning. The base `ListView.watch_index` always scrolls to the highlighted item.

2. **Use `self.scroll_y = ...` not `self.scroll_to()`** for same-frame scroll+index changes. `scroll_to` may animate or defer, causing flicker.

3. **`event.prevent_default()` AND `event.stop()`** — call both in `on_key` for consumed keys. `prevent_default()` stops the default action (like typing in an input), `stop()` prevents the event from bubbling to parent widgets/bindings.

4. **`scrollable_content_region.height`** gives the viewport height in rows (excluding scrollbar). Use this, not `self.size.height` or `self.region.height`.

5. **Item height calculation** uses `self.children[0].region.height`. This assumes uniform item heights. If items have variable height, you'll need a different approach.

6. **`call_after_refresh`** — use this when you need to defer work until after DOM changes have been applied (e.g., scrolling to a newly mounted widget).
