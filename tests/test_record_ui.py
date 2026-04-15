"""UI tests for the record-direct-into-app flow."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from textual.app import App, ComposeResult
from textual.widgets import Static

from easytrans.app import EasyTransApp, RecordingModal
from easytrans.config import EasyTransConfig, RecorderConfig, RecordingConfig, WhisperConfig
from easytrans.models import Base


def _make_app(tmp_path: Path) -> EasyTransApp:
    config = EasyTransConfig(
        data_dir=tmp_path / "data",
        recorder=RecorderConfig(),
        whisper=WhisperConfig(),
        recording=RecordingConfig(),
    )
    config.ensure_dirs()
    app = EasyTransApp(config=config)
    engine = create_engine(f"sqlite:///{config.db_path}")
    Base.metadata.create_all(engine)
    app.engine = engine
    return app


@dataclass
class _FakeRecorder:
    """
    Minimal stand-in for Recorder used by modal unit tests.

    The modal only reaches into `.elapsed_seconds` — no real audio I/O
    is needed to drive the state machine.
    """
    elapsed_seconds: float = 0.0


class _ModalHostApp(App):
    """Tiny host app that owns a single RecordingModal for focused tests."""

    def __init__(self, recorder: _FakeRecorder) -> None:
        super().__init__()
        self.recorder = recorder
        self.modal: RecordingModal | None = None

    def compose(self) -> ComposeResult:
        return []

    async def on_mount(self) -> None:
        self.modal = RecordingModal(self.recorder)  # type: ignore[arg-type]
        await self.push_screen(self.modal)


class _FakeInputStream:
    """Fake sounddevice.InputStream for tests that exercise the real Recorder."""

    instances: list["_FakeInputStream"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.callback = kwargs["callback"]
        self.started = False
        self.closed = False
        _FakeInputStream.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_input_stream():
    _FakeInputStream.instances.clear()
    with patch("easytrans.recording.sd.InputStream", _FakeInputStream):
        yield _FakeInputStream


# --- Modal state-machine tests (no real recorder) ---


@pytest.mark.asyncio
async def test_modal_save_sets_done_event() -> None:
    recorder = _FakeRecorder(elapsed_seconds=3.0)
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("space")
        await pilot.pause()
        assert app.modal is not None
        assert app.modal.cancelled is False
        assert app.modal.done_event.is_set()


@pytest.mark.asyncio
async def test_modal_enter_also_saves() -> None:
    recorder = _FakeRecorder(elapsed_seconds=3.0)
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert app.modal is not None
        assert app.modal.cancelled is False
        assert app.modal.done_event.is_set()


@pytest.mark.asyncio
async def test_modal_escape_under_threshold_cancels_immediately() -> None:
    recorder = _FakeRecorder(elapsed_seconds=2.0)  # < 10
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        assert app.modal is not None
        assert app.modal.cancelled is True
        assert app.modal.done_event.is_set()


@pytest.mark.asyncio
async def test_modal_escape_over_threshold_asks_confirmation() -> None:
    recorder = _FakeRecorder(elapsed_seconds=30.0)
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        assert app.modal is not None
        # Confirmation state: done_event NOT yet set, discard flag on.
        assert app.modal._confirming_discard is True
        assert app.modal.done_event.is_set() is False
        title = app.modal.query_one("#rec-title", Static)
        assert "Discard" in str(title.render())


@pytest.mark.asyncio
async def test_modal_confirm_n_returns_to_recording() -> None:
    recorder = _FakeRecorder(elapsed_seconds=30.0)
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.modal is not None
        assert app.modal._confirming_discard is False
        assert app.modal.done_event.is_set() is False


@pytest.mark.asyncio
async def test_modal_confirm_y_cancels() -> None:
    recorder = _FakeRecorder(elapsed_seconds=30.0)
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert app.modal is not None
        assert app.modal.cancelled is True
        assert app.modal.done_event.is_set()


@pytest.mark.asyncio
async def test_modal_save_ignored_during_confirm() -> None:
    """Space during discard confirmation must not secretly save."""
    recorder = _FakeRecorder(elapsed_seconds=30.0)
    app = _ModalHostApp(recorder)
    async with app.run_test() as pilot:
        await pilot.press("escape")   # enter confirm
        await pilot.pause()
        await pilot.press("space")    # should be a no-op
        await pilot.pause()
        assert app.modal is not None
        assert app.modal.done_event.is_set() is False


# --- End-to-end: pressing 'a' in the real app opens the modal ---


@pytest.mark.asyncio
async def test_pressing_a_opens_recording_modal(
    tmp_path: Path, fake_input_stream
) -> None:
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()

        # The worker thread starts the recorder → a fake stream exists.
        for _ in range(20):
            if fake_input_stream.instances:
                break
            await pilot.pause(0.05)
        assert fake_input_stream.instances, "Recorder.start() was never called"
        assert fake_input_stream.instances[0].started is True

        # A RecordingModal is on top of the screen stack.
        top = app.screen
        assert isinstance(top, RecordingModal)

        # Clean up: press escape to cancel so the worker exits gracefully.
        await pilot.press("escape")
        for _ in range(40):
            await pilot.pause(0.05)
            if not isinstance(app.screen, RecordingModal):
                break


@pytest.mark.asyncio
async def test_pressing_a_disabled_during_playback(tmp_path: Path) -> None:
    """Playback and recording compete for audio devices — gate them."""
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        # Fake an active player
        app._player = object()  # type: ignore[assignment]
        try:
            assert app.check_action("record", ()) is False
        finally:
            app._player = None
