"""Tests for transcription utilities."""

import multiprocessing
import os
from pathlib import Path

from easytrans.config import load_config
from easytrans.transcribe import format_timestamp, segments_to_text


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3661) == "61:01"


def test_segments_to_text_clean() -> None:
    segments = [
        {"start": 0.0, "end": 2.0, "text": "Hello"},
        {"start": 2.0, "end": 4.0, "text": "world"},
    ]
    assert segments_to_text(segments) == "Hello world"


def test_segments_to_text_with_timestamps() -> None:
    segments = [
        {"start": 0.0, "end": 2.0, "text": "Hello"},
        {"start": 65.0, "end": 70.0, "text": "world"},
    ]
    result = segments_to_text(segments, include_timestamps=True)
    assert result == "[00:00] Hello\n[01:05] world"


def test_segments_to_text_empty() -> None:
    assert segments_to_text([]) == ""
    assert segments_to_text([], include_timestamps=True) == ""


def test_config_cpu_threads_default(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    assert config.whisper.cpu_threads == 4


def test_config_cpu_threads_roundtrip(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'data_dir = "~/x"\n'
        "[whisper]\n"
        "cpu_threads = 2\n"
    )
    config = load_config(cfg_path)
    assert config.whisper.cpu_threads == 2


def _env_check_worker(result_queue: multiprocessing.Queue) -> None:
    """Helper that runs in a child process to snapshot env vars at the moment
    `_whisper_worker` tries to import `faster_whisper`."""
    import unittest.mock

    captured: dict[str, str | None] = {}

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == "faster_whisper":
            for key in ("CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
                captured[key] = os.environ.get(key)
            raise ImportError("mock: stop here")
        return original_import(name, *args, **kwargs)

    with unittest.mock.patch("builtins.__import__", side_effect=mock_import):
        from easytrans.transcribe import _whisper_worker
        q: multiprocessing.Queue = multiprocessing.Queue()
        try:
            _whisper_worker("dummy.wav", "tiny", 3, q)
        except ImportError:
            pass

    result_queue.put(captured)


def test_env_vars_set_before_whisper_import() -> None:
    """Regression: CUDA_VISIBLE_DEVICES and thread caps must be set BEFORE
    faster_whisper is imported.

    - CUDA_VISIBLE_DEVICES: CTranslate2 probes the GPU at import time; on a
      loaded display GPU this can crash the NVIDIA driver.
    - OMP_NUM_THREADS / MKL_NUM_THREADS: OpenMP reads the thread count once at
      runtime init, and sustained all-core AVX2 load has been observed to
      hard-lock the dev machine (see SPEC.md "Known hardware issue").
    """
    q: multiprocessing.Queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_env_check_worker, args=(q,))
    p.start()
    p.join(timeout=10)
    captured = q.get_nowait()
    assert captured["CUDA_VISIBLE_DEVICES"] == "", (
        f"CUDA_VISIBLE_DEVICES should be '' before faster_whisper import, got "
        f"{captured['CUDA_VISIBLE_DEVICES']!r}"
    )
    assert captured["OMP_NUM_THREADS"] == "3", (
        f"OMP_NUM_THREADS should be '3' (matching cpu_threads) before faster_whisper "
        f"import, got {captured['OMP_NUM_THREADS']!r}"
    )
    assert captured["MKL_NUM_THREADS"] == "3", (
        f"MKL_NUM_THREADS should be '3' before faster_whisper import, got "
        f"{captured['MKL_NUM_THREADS']!r}"
    )
