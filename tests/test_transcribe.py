"""Tests for transcription utilities."""

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
