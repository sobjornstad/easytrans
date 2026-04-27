# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

- Run the app: `uv run easytrans` (entry point: `src/easytrans/__main__.py`)
- Run all tests: `uv run pytest`
- Run a single test: `uv run pytest tests/test_app.py::test_name`
- Type check: `uv run pyright` (config in `pyproject.toml`, `typeCheckingMode = "basic"`)
- New Alembic migration: `uv run alembic revision --autogenerate -m "msg"`
  — migrations live at `src/easytrans/migrations/versions/` and run automatically
  inside `get_engine()`; the app never calls `alembic upgrade` directly.

## Architecture

- **TUI** lives in `src/easytrans/app.py`. `EasyTransApp` composes a `MemoTable`
  (a `DataTable` subclass with a vim-style navigation layer — see
  `spec/VIM-NAVIGATION.md` before changing key handling) and a `MemoPreview`
  (a `VerticalScroll`).
- **Persistence**: SQLite via SQLAlchemy ORM in `models.py` (`Memo`,
  `Transcription`, `SourceFile`). DB lives at `{data_dir}/easytrans.db`. Schema
  changes go through Alembic; `get_engine()` runs `command.upgrade(..., "head")`
  on startup.
- **Three audio-ingestion paths** all converge on
  `import_audio_as_memo()` in `importer.py`: USB-recorder sync (`sync.py`),
  in-app live recording (`recording.py` driven by `app._do_record`), and any
  one-off import. Identity is the SHA-256 file hash (PK on `Memo`); the
  `SourceFile` table caches `(filename, size, mtime_ns) → hash` so a re-sync
  of the same recorder doesn't re-read every file off USB.
- **Transcription** (`transcribe.py`) runs `faster_whisper` in a separate
  `multiprocessing.Process`, *not* just a thread. This is so:
  (a) it can be killed on quit (processes are tracked in
  `app._active_processes`), and (b) `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and
  `CUDA_VISIBLE_DEVICES=""` can be set *before* `ctranslate2` imports — OpenMP
  reads thread counts once at init.
- **Tiered transcription**: every new memo gets a fast `initial_model` pass,
  then a background upgrade pass to `default_model` driven by
  `get_memos_needing_upgrade()`, and the `r` keybinding triggers a one-off
  `large_model` re-transcription on the selected memo.
- **Threading**: long work uses Textual's `@work(thread=True, exclusive=True, group=...)`.
  UI mutations from worker threads must go through `self.call_from_thread(...)`.
  `app._shutting_down: threading.Event` is the cooperative cancel signal —
  workers check it between memos.
- **Playback**: `playback.py` wraps python-mpv behind an `AudioPlayer` Protocol;
  tests override `EasyTransApp._make_audio_player` to inject a stub.
  Transcripts are stored in the DB as `[MM:SS] text` lines (parsed by
  `parse_segments`); the user-facing `.md` file holds the clean text only.
- **On-disk layout**: `{data_dir}/audio/{year}/{file_id}.{ext}` and
  `{data_dir}/text/{year}/{file_id}.md`. The source extension is whatever the
  recorder produces (`.mp3`, `.wma`, …); a `.wav` sibling is created for
  Whisper unless the source is itself WAV (in-app live recording case).

## Hardware caveat — load-bearing

`whisper.cpu_threads` in `~/.config/easytrans/config.toml` (default 4 on a
10C/20T box) is intentionally capped well below core count. The dev machine
hard-locks under sustained all-core AVX2 load from `ctranslate2` — total
freeze, ACPI BERT hardware-error record on reboot. **Do not raise this
without sustained-load testing, and do not set it to 0 ("all cores").** If a
recurrence happens after this cap is in place, drop to 2 or 1 before
suspecting a software regression. The real fix is BIOS-level
(PL1/PL2/Tau, modest undervolt) — full write-up in `spec/SPEC.md`.

## Specs and design docs

- `spec/SPEC.md` — product/UX spec and the canonical hardware-issue note.
- `spec/PLAN.md` — phased implementation plan.
- `spec/TODO.txt` — current backlog and known bugs.
- `spec/VIM-NAVIGATION.md` — design doc for `MemoTable`'s vim layer; read
  before changing key handling or scroll behavior.

## Testing

Interactively test your changes as you go using the `tmux-tui` skill.
Also write unit tests of the data/mechanical layer
and UI tests using Textual's UI testing framework,
that verify the expected behavior.

If during development you encounter a bug that could plausibly be re-introduced,
add a failing regression test before fixing it.

`tests/conftest.py` provides `tmp_data_dir`, `db_engine` (in-memory SQLite with
`Base.metadata.create_all`), and `db_session` fixtures. UI tests use Textual's
pilot harness; tests that need playback override `_make_audio_player`.
