"""Tests for the live Recorder.

These tests stub out sounddevice so they don't touch real audio hardware
(CI and the Vagrant VM may not have a usable input device).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from easytrans.config import EasyTransConfig, RecordingConfig
from easytrans.recording import Recorder


def _config(tmp_path: Path) -> EasyTransConfig:
    cfg = EasyTransConfig(
        data_dir=tmp_path / "data",
        recording=RecordingConfig(device=None, samplerate=16000),
    )
    cfg.ensure_dirs()
    return cfg


class _FakeStream:
    """Stand-in for sounddevice.InputStream.

    Captures the callback and lets tests push synthetic frames in.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.callback = kwargs["callback"]
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def push_silence(self, frames: int) -> None:
        "Invoke the callback with `frames` samples of silence."
        buf = np.zeros((frames, 1), dtype=np.int16)
        self.callback(buf, frames, None, None)


@pytest.fixture
def fake_stream_factory():
    """Patch sd.InputStream with a capturable fake and yield the factory."""
    created: list[_FakeStream] = []

    def factory(*args, **kwargs):
        stream = _FakeStream(*args, **kwargs)
        created.append(stream)
        return stream

    with patch("easytrans.recording.sd.InputStream", side_effect=factory):
        yield created


def test_start_creates_wav_in_tmp_dir(
    tmp_path: Path, fake_stream_factory
) -> None:
    cfg = _config(tmp_path)
    r = Recorder(cfg)
    path = r.start()
    try:
        assert path.parent == cfg.recording_tmp_dir
        assert path.suffix == ".wav"
        assert path.exists()
        assert fake_stream_factory[0].started is True
    finally:
        r.cancel()


def test_stop_finalizes_wav_with_frames(
    tmp_path: Path, fake_stream_factory
) -> None:
    cfg = _config(tmp_path)
    r = Recorder(cfg)
    path = r.start()
    # Feed half a second of silence through the callback.
    fake_stream_factory[0].push_silence(8000)
    returned = r.stop()

    assert returned == path
    assert path.exists()
    # Readable as a WAV with the expected shape.
    data, samplerate = sf.read(str(path), dtype="int16")
    assert samplerate == 16000
    assert len(data) == 8000
    assert fake_stream_factory[0].closed is True


def test_cancel_removes_staging_file(
    tmp_path: Path, fake_stream_factory
) -> None:
    cfg = _config(tmp_path)
    r = Recorder(cfg)
    path = r.start()
    fake_stream_factory[0].push_silence(1000)
    r.cancel()

    assert not path.exists()
    assert fake_stream_factory[0].closed is True


def test_elapsed_seconds_progresses(
    tmp_path: Path, fake_stream_factory
) -> None:
    cfg = _config(tmp_path)
    r = Recorder(cfg)
    assert r.elapsed_seconds == 0.0
    r.start()
    try:
        time.sleep(0.05)
        assert r.elapsed_seconds > 0.0
    finally:
        r.cancel()


def test_stop_is_idempotent(
    tmp_path: Path, fake_stream_factory
) -> None:
    cfg = _config(tmp_path)
    r = Recorder(cfg)
    path = r.start()
    fake_stream_factory[0].push_silence(500)
    first = r.stop()
    second = r.stop()
    assert first == second == path
