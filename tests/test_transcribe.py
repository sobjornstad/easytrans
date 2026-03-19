"""Tests for transcription utilities."""

import multiprocessing
import os

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


def _cuda_check_worker(result_queue: multiprocessing.Queue) -> None:
    """Helper that runs in a child process to check CUDA env inside _whisper_worker."""
    import unittest.mock

    captured: dict[str, str | None] = {}

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == "faster_whisper":
            # Capture the env var at the moment faster_whisper would be imported
            captured["CUDA_VISIBLE_DEVICES"] = os.environ.get("CUDA_VISIBLE_DEVICES")
            raise ImportError("mock: stop here")
        return original_import(name, *args, **kwargs)

    with unittest.mock.patch("builtins.__import__", side_effect=mock_import):
        from easytrans.transcribe import _whisper_worker
        q: multiprocessing.Queue = multiprocessing.Queue()
        try:
            _whisper_worker("dummy.wav", "tiny", q)
        except ImportError:
            pass

    result_queue.put(captured.get("CUDA_VISIBLE_DEVICES"))


def test_cuda_disabled_before_whisper_import() -> None:
    """Regression: CUDA_VISIBLE_DEVICES must be set before importing faster_whisper.

    CTranslate2 probes the GPU at import time; on a loaded display GPU this can
    crash the NVIDIA driver. See commit history for details.
    """
    q: multiprocessing.Queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_cuda_check_worker, args=(q,))
    p.start()
    p.join(timeout=10)
    value = q.get_nowait()
    assert value == "", (
        f"CUDA_VISIBLE_DEVICES should be '' before faster_whisper import, got {value!r}"
    )
