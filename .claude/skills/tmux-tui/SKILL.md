---
name: tmux-tui
description: >
  Test TUI applications via tmux. Use when working on a TUI application and
  asked to: interactively test, check the UI, verify the interface, take a
  screenshot, capture the screen, send keys, send keystrokes, restart the TUI,
  resize the terminal, check for visual regressions, interact with the terminal
  UI, or debug the display.
version: 1.0.0
---

# tmux TUI Testing

This skill provides a single script that wraps all tmux operations for testing TUI applications. All interactions go through this one entry point, requiring only one permission entry.

**Script path:** `scripts/tui.sh` (relative to this skill's root directory)

All examples below show the script as `tui.sh` for brevity — always use the full resolved path above when running commands.

The TUI command to launch is read from `.tmux-tui-command` in the project root.

## IMPORTANT

**NEVER edit or write to `.tmux-tui-command`.** This file is controlled exclusively by the user. It defines what command the TUI testing script runs. Do not modify it under any circumstances.

**Each Bash call MUST contain exactly one `tui.sh` invocation — no chaining.** Do NOT use `&&`, `||`, `;`, pipes, or subshells to combine a `tui.sh` call with other commands. The script already prints everything you need to stdout. Chaining breaks the "always allow" permission grant and forces re-prompting. Bad examples:

```
SESSION=$(tui.sh start) && echo "$SESSION"   # WRONG
tui.sh capture $SESSION | cat                 # WRONG
```

Correct — just call the script alone:

```bash
tui.sh start
```

Note: shell variable state does NOT persist between Bash tool calls. Do not try to save the session ID in a variable. Instead, read the session ID from the `start` output and substitute it directly into later commands.

## Workflow

The standard testing pattern (each line is a **separate** Bash tool call):

```bash
# 1. Start a session (prints the session ID, e.g. "tui-a1b2c3")
tui.sh start

# 2. Wait for startup, then capture the screen
tui.sh send-and-capture tui-a1b2c3 2 ""

# 3. Interact with the TUI
tui.sh send-keys tui-a1b2c3 Tab
tui.sh send-keys tui-a1b2c3 C-c b c

# 4. Capture the result
tui.sh capture tui-a1b2c3

# 5. After making code changes, restart the TUI
# Prefer restarting to stopping and then starting again in most cases,
# unless you want a completely clean session with no lingering database state, etc.
tui.sh restart tui-a1b2c3

# 6. When done testing, stop the session
tui.sh stop tui-a1b2c3
```

## Subcommand Reference

### `start`

Create a new tmux session, launch the TUI, and print the session name to stdout.

```bash
tui.sh start
```

- Creates a session named `tui-XXXXXX` (random hex suffix)
- Default terminal size: 120x40
- The TUI command is read from `.tmux-tui-command`
- The `$id` variable in the command is replaced with the session name

### `send-keys SESSION KEY [KEY...]`

Send keystrokes to the TUI.

```bash
# Single key
tui.sh send-keys $SESSION Enter

# Multiple keys in sequence
tui.sh send-keys $SESSION Tab Tab Enter

# Repeat a key (useful for scrolling)
tui.sh send-keys $SESSION --repeat 10 Down

# Send literal text followed by Enter
tui.sh send-keys $SESSION "Hello World" Enter

# Control sequences
tui.sh send-keys $SESSION C-a
```

### `capture SESSION`

Capture the visible screen as plain text (no ANSI escape codes).

```bash
tui.sh capture $SESSION
```

### `send-and-capture SESSION DELAY KEY [KEY...]`

Send keys, wait for the specified delay (in seconds), then capture the screen. This is a single atomic operation — use this instead of chaining `send-keys && sleep && capture`.

```bash
# Send Tab, wait 1 second, capture
tui.sh send-and-capture $SESSION 1 Tab

# Send Enter, wait 2 seconds, capture
tui.sh send-and-capture $SESSION 2 Enter

# Scroll down 5 times, wait 0.5 seconds, capture
tui.sh send-and-capture $SESSION 0.5 --repeat 5 Down

# Wait for startup without sending meaningful keys
tui.sh send-and-capture $SESSION 2 ""
```

### `restart SESSION`

Kill the TUI process (using escalating signals: INT → TERM → QUIT → KILL) and re-launch the command. The tmux session stays alive.

```bash
tui.sh restart $SESSION
```

Use this after making code changes to test the updated TUI without creating a new session.

### `stop SESSION`

Kill the entire tmux session. Only works on sessions with the `tui-` prefix.

```bash
tui.sh stop $SESSION
```

### `resize SESSION WIDTH HEIGHT`

Resize the tmux window. Useful for testing responsive layouts and display breakpoints.

```bash
# Standard 80-column terminal
tui.sh resize $SESSION 80 24

# Wide terminal
tui.sh resize $SESSION 200 50

# Narrow terminal (test responsive layout)
tui.sh resize $SESSION 60 20
```

### `list`

List all active `tui-*` sessions (useful for finding orphaned sessions).

```bash
tui.sh list
```

## Key Reference

tmux key names for use with `send-keys` and `send-and-capture`:

| Key | tmux name |
|---|---|
| Enter/Return | `Enter` |
| Tab | `Tab` |
| Escape | `Escape` |
| Space | `Space` |
| Backspace | `BSpace` |
| Up Arrow | `Up` |
| Down Arrow | `Down` |
| Left Arrow | `Left` |
| Right Arrow | `Right` |
| Home | `Home` |
| End | `End` |
| Page Up | `PPage` |
| Page Down | `NPage` |
| Ctrl+A | `C-a` |
| Ctrl+C | `C-c` |
| Ctrl+X | `C-x` |
| Ctrl+Z | `C-z` |
| Ctrl+D | `C-d` |

**Notes:**
- Control sequences use tmux's `C-` prefix notation — `C-a` is a single key argument
- Multi-key sequences are sent as separate arguments: `send-keys SESSION C-a x` sends Ctrl+A then x
- Quoted strings are sent as literal text: `send-keys SESSION "Hello World" Enter`
- Use `--repeat N` before keys to repeat them: `send-keys SESSION --repeat 15 Down`

## Tips

- **Always capture after sending keys** to verify the TUI responded correctly
- **Use `send-and-capture`** for atomic operations — avoids permission issues with chained commands
- **Use `--repeat`** for scrolling instead of sending the same key many times
- **Use `resize`** to test how the TUI handles different terminal sizes
- **Wait for startup**: After `start`, use `send-and-capture <session-id> 2 ""` to wait for the TUI to initialize before interacting
- **After code changes**: Use `restart` to relaunch the TUI in the same session rather than `stop` + `start`
