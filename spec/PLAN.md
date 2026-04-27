# EasyTrans Implementation Plan

## Context

EasyTrans is a CLI voice memo transcription manager. The user records memos on a handheld USB mass storage recorder, syncs them to their computer, and manages transcriptions through a Textual TUI. The priority is getting a working transcription pipeline first, deferring integrations and advanced features (audio playback, LLM cleanup, send-to-X integrations).

## Decisions Made

- **Whisper**: faster-whisper (CTranslate2-based)
- **Recorder**: USB mass storage device (mounts as a regular filesystem)
- **Config**: `~/.config/easytrans/config.toml` (XDG)
- **Docstrings**: Plain descriptive style
- **Versions**: No `.versions` file on disk; multiple transcription versions stored in DB only
- **Timestamps**: Clean text in `.md` files (no timestamp markers); timestamps stored in DB transcription records
- **SQLAlchemy**: ORM with mapped classes
- **Project structure**: `src/easytrans/`, `tests/`, `docs/`

## Implementation Phases

### Phase 1: Project Scaffolding
**Files**: `pyproject.toml`, `src/easytrans/__init__.py`, `src/easytrans/__main__.py`

- `uv init` with src layout
- Add dependencies: `sqlalchemy`, `textual`, `faster-whisper`, `tomli` (or `tomllib` on 3.11+)
- Add dev dependencies: `pytest`, `pyright`
- Configure pytest in `pyproject.toml` (with `--doctest-modules`)
- Configure pyright in `pyproject.toml`
- Minimal `__main__.py` entry point that launches the Textual app

### Phase 2: Configuration
**Files**: `src/easytrans/config.py`

- TOML config at `~/.config/easytrans/config.toml`
- Key settings:
  - `data_dir`: path to the `transcriptions/` root (where audio/ and text/ live)
  - `device_path`: `/dev/sdX` for the recorder
  - `mount_point`: where to mount the device
  - `recorder_audio_dir`: relative path within the mounted device where recordings are stored
  - `default_model`: whisper model name for initial transcription (e.g., `tiny` or `base`)
  - `large_model`: model for re-transcription (e.g., `medium` or `large-v3`)
- Load config with sensible defaults; create default config file if missing

### Phase 3: Database & Data Model
**Files**: `src/easytrans/db.py`, `src/easytrans/models.py`

- SQLAlchemy ORM models:
  - `Memo`: `file_hash` (str PK), `file_id` (str unique), `recorded_at` (datetime), `synced_at` (datetime), `completed` (bool default False)
  - `Transcription`: `id` (int PK), `memo_hash` (FK to Memo), `transcribed_at` (datetime), `model_name` (str), `text` (str, with timestamp annotations)
- DB stored at `{data_dir}/easytrans.db`
- Session management with context manager
- Helper queries: get all memos (optionally filtering completed), get transcriptions for a memo, check if hash exists

### Phase 4: Sync & File Management
**Files**: `src/easytrans/sync.py`, `src/easytrans/files.py`

- **File ID generation** (`files.py`):
  - Scan `audio/{year}/` to find the next sequential number for a given year
  - Generate `YYYY-NNNN` format IDs
- **Sync workflow** (`sync.py`):
  1. Mount device at configured mount point (via `mount` subprocess)
  2. Scan recorder directory for audio files
  3. Hash each file (SHA-256); skip files already in DB
  4. For each new file: assign ID, copy to `audio/{year}/`, record in DB with fs timestamp
  5. Unmount device
  6. Return list of newly synced memos for transcription
- **Duplicate detection**: SHA-256 hash of file contents as primary key

### Phase 5: Audio Conversion & Transcription
**Files**: `src/easytrans/transcribe.py`

- **WAV conversion**: ffmpeg subprocess to convert source audio to 16kHz mono WAV (Whisper's expected format), output alongside the source in `audio/{year}/`
- **Transcription**:
  - Load faster-whisper model (configurable size)
  - Transcribe WAV, capture segments with timestamps
  - Store full text (with timestamp data) in `transcriptions` DB table
  - Write clean text (no timestamps) to `text/{year}/{id}.md`
- **Parallel processing**: use `concurrent.futures.ThreadPoolExecutor` to convert + transcribe multiple files after sync
- **Re-transcription**: load a larger model, create new `Transcription` record, optionally overwrite `.md`

### Phase 6: TUI (Textual)
**Files**: `src/easytrans/app.py`, `src/easytrans/widgets.py` (if needed)

- **Main screen layout**:
  - `DataTable` at top: columns for ID, status (checkbox/icon), date/time, first line of text
  - `RichLog` or `Static` preview pane below showing selected memo's full text
- **Keybindings**:
  - `s` - Sync from recorder (runs sync + transcription in background worker)
  - `t` - Transcribe arbitrary file (file picker or path input)
  - `h` - Toggle show/hide completed
  - `e` - Edit text (open `.md` in `$EDITOR` via subprocess)
  - `r` - Re-transcribe with larger model
  - `c` - Copy text to clipboard (clean)
  - `C` - Copy text with timestamps
  - `d` / `Enter` - Mark complete
  - `q` - Quit
- **Background workers**: Textual workers for sync and transcription so the UI stays responsive
- **Status indicators**: show transcription progress in a footer/status bar

## File Tree (planned)

```
easytrans/
  pyproject.toml
  src/
    easytrans/
      __init__.py
      __main__.py
      app.py          # Textual application
      config.py       # Configuration loading
      db.py           # Database session management
      models.py       # SQLAlchemy ORM models
      sync.py         # Device sync workflow
      files.py        # File ID generation, hashing, path helpers
      transcribe.py   # Audio conversion & Whisper transcription
  tests/
    conftest.py
    test_files.py
    test_sync.py
    test_transcribe.py
    test_db.py
  docs/
```

## Verification

- **Unit tests**: file hashing, ID generation (`YYYY-NNNN` sequencing), duplicate detection, DB CRUD operations, config loading
- **Integration tests**: sync workflow with a mock/temp filesystem (no real device), transcription pipeline with a short test audio file
- **Manual testing**: launch the TUI, verify table rendering, keybindings, and background transcription feedback
- **Type checking**: `pyright` passes clean
