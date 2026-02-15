#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
# Claude Code always runs Bash from the project working directory
PROJECT_ROOT="$(pwd)"

COMMAND_FILE="$PROJECT_ROOT/.tmux-tui-command"
DEFAULT_WIDTH=120
DEFAULT_HEIGHT=40

# --- Helpers ---

die() {
    echo "error: $*" >&2
    exit 1
}

validate_session_name() {
    local name="$1"
    [[ "$name" =~ ^[a-zA-Z0-9_-]+$ ]] || die "invalid session name: '$name' (must match ^[a-zA-Z0-9_-]+\$)"
}

require_tui_prefix() {
    local name="$1"
    [[ "$name" == tui-* ]] || die "session '$name' does not have required 'tui-' prefix"
}

session_exists() {
    tmux has-session -t "$1" 2>/dev/null
}

require_session() {
    local name="$1"
    validate_session_name "$name"
    require_tui_prefix "$name"
    session_exists "$name" || die "session '$name' does not exist"
}

read_command() {
    [[ -f "$COMMAND_FILE" ]] || die "command file not found: $COMMAND_FILE"
    local cmd
    cmd="$(cat "$COMMAND_FILE")"
    [[ -n "$cmd" ]] || die "command file is empty: $COMMAND_FILE"
    echo "$cmd"
}

expand_command() {
    local cmd="$1"
    local session_id="$2"
    # Replace \$id with a placeholder, then $id with session ID, then restore placeholder
    local placeholder=$'\x01LITERAL_DOLLAR_ID\x01'
    cmd="${cmd//\\\$id/$placeholder}"
    cmd="${cmd//\$id/$session_id}"
    cmd="${cmd//$placeholder/\$id}"
    echo "$cmd"
}

generate_session_name() {
    local hex
    if [[ -r /dev/urandom ]]; then
        hex=$(head -c 3 /dev/urandom | xxd -p)
    else
        hex=$(printf '%06x' $RANDOM)
    fi
    echo "tui-${hex}"
}

# --- Subcommands ---

cmd_start() {
    local session_name
    session_name="$(generate_session_name)"

    local cmd
    cmd="$(read_command)"
    cmd="$(expand_command "$cmd" "$session_name")"

    # Create detached session with a shell (not the TUI command directly)
    tmux new-session -d -s "$session_name" -x "$DEFAULT_WIDTH" -y "$DEFAULT_HEIGHT"

    # Send the command to the shell
    tmux send-keys -t "$session_name" "$cmd" Enter

    echo "$session_name"
}

cmd_send_keys() {
    local session="$1"; shift
    require_session "$session"

    local repeat=1
    local keys=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --repeat)
                [[ $# -ge 2 ]] || die "--repeat requires a number"
                repeat="$2"
                [[ "$repeat" =~ ^[0-9]+$ ]] || die "--repeat value must be a positive integer"
                shift 2
                ;;
            *)
                keys+=("$1")
                shift
                ;;
        esac
    done

    [[ ${#keys[@]} -gt 0 ]] || die "no keys specified"

    for ((i = 0; i < repeat; i++)); do
        for key in "${keys[@]}"; do
            tmux send-keys -t "$session" "$key"
        done
    done
}

cmd_capture() {
    local session="$1"
    require_session "$session"
    tmux capture-pane -t "$session" -p
}

cmd_send_and_capture() {
    local session="$1"; shift
    require_session "$session"

    local delay="$1"; shift
    [[ "$delay" =~ ^[0-9]*\.?[0-9]+$ ]] || die "delay must be a number (seconds)"

    local repeat=1
    local keys=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --repeat)
                [[ $# -ge 2 ]] || die "--repeat requires a number"
                repeat="$2"
                [[ "$repeat" =~ ^[0-9]+$ ]] || die "--repeat value must be a positive integer"
                shift 2
                ;;
            *)
                keys+=("$1")
                shift
                ;;
        esac
    done

    [[ ${#keys[@]} -gt 0 ]] || die "no keys specified"

    for ((i = 0; i < repeat; i++)); do
        for key in "${keys[@]}"; do
            tmux send-keys -t "$session" "$key"
        done
    done

    sleep "$delay"
    tmux capture-pane -t "$session" -p
}

cmd_restart() {
    local session="$1"
    require_session "$session"

    # Get the shell PID in the pane
    local shell_pid
    shell_pid="$(tmux display-message -t "$session" -p '#{pane_pid}')"

    # Find child processes (the TUI app)
    local tui_pids
    tui_pids="$(pgrep -P "$shell_pid" 2>/dev/null || true)"

    # Kill each child with escalating signals
    for tui_pid in $tui_pids; do
        for sig in INT TERM QUIT KILL; do
            if kill -0 "$tui_pid" 2>/dev/null; then
                kill -"$sig" "$tui_pid" 2>/dev/null || true
                if [[ "$sig" != "KILL" ]]; then
                    sleep 1
                else
                    # Wait briefly for KILL to take effect
                    sleep 0.5
                fi
            else
                break
            fi
        done
    done

    # Wait a moment for the shell to settle
    sleep 0.5

    # Re-send the command
    local cmd
    cmd="$(read_command)"
    cmd="$(expand_command "$cmd" "$session")"
    tmux send-keys -t "$session" "$cmd" Enter
}

cmd_stop() {
    local session="$1"
    validate_session_name "$session"
    require_tui_prefix "$session"
    tmux kill-session -t "$session" 2>/dev/null || true
}

cmd_resize() {
    local session="$1"
    require_session "$session"

    local width="$2"
    local height="$3"
    [[ "$width" =~ ^[0-9]+$ ]] || die "width must be a positive integer"
    [[ "$height" =~ ^[0-9]+$ ]] || die "height must be a positive integer"

    tmux resize-window -t "$session" -x "$width" -y "$height"
}

cmd_list() {
    tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^tui-' || true
}

# --- Main ---

usage() {
    cat <<'EOF'
Usage: tui.sh <subcommand> [args...]

Subcommands:
  start                                  Create session, launch TUI, output session name
  send-keys SESSION KEY [KEY...]         Send keystrokes (supports --repeat N)
  capture SESSION                        Capture visible screen as plain text
  send-and-capture SESSION DELAY KEY ... Send keys, wait, then capture
  restart SESSION                        Kill TUI process and re-launch
  stop SESSION                           Kill the tmux session
  resize SESSION WIDTH HEIGHT            Resize the tmux window
  list                                   List active tui-* sessions
EOF
}

[[ $# -ge 1 ]] || { usage; exit 1; }

subcommand="$1"; shift

case "$subcommand" in
    start)
        cmd_start
        ;;
    send-keys)
        [[ $# -ge 2 ]] || die "usage: tui.sh send-keys SESSION KEY [KEY...]"
        cmd_send_keys "$@"
        ;;
    capture)
        [[ $# -ge 1 ]] || die "usage: tui.sh capture SESSION"
        cmd_capture "$1"
        ;;
    send-and-capture)
        [[ $# -ge 3 ]] || die "usage: tui.sh send-and-capture SESSION DELAY KEY [KEY...]"
        cmd_send_and_capture "$@"
        ;;
    restart)
        [[ $# -ge 1 ]] || die "usage: tui.sh restart SESSION"
        cmd_restart "$1"
        ;;
    stop)
        [[ $# -ge 1 ]] || die "usage: tui.sh stop SESSION"
        cmd_stop "$1"
        ;;
    resize)
        [[ $# -ge 3 ]] || die "usage: tui.sh resize SESSION WIDTH HEIGHT"
        cmd_resize "$1" "$2" "$3"
        ;;
    list)
        cmd_list
        ;;
    *)
        die "unknown subcommand: $subcommand"
        ;;
esac
