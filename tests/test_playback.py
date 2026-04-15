"""Unit tests for the playback module."""

from pathlib import Path

from easytrans.playback import (
    Segment,
    StubAudioPlayer,
    find_segment_index,
    parse_segments,
)


def test_parse_segments_basic():
    text = "[00:00] First line\n[00:05] Second line\n[01:23] Third line"
    segs = parse_segments(text)
    assert segs == [
        Segment(start=0.0, text="First line"),
        Segment(start=5.0, text="Second line"),
        Segment(start=83.0, text="Third line"),
    ]


def test_parse_segments_empty():
    assert parse_segments("") == []


def test_parse_segments_skips_unparseable_lines():
    text = "header\n[00:00] real\nnot a segment\n[00:10] another"
    segs = parse_segments(text)
    assert [s.text for s in segs] == ["real", "another"]
    assert [s.start for s in segs] == [0.0, 10.0]


def test_parse_segments_handles_extra_whitespace():
    text = "[00:00]   leading spaces"
    segs = parse_segments(text)
    assert segs == [Segment(start=0.0, text="leading spaces")]


def test_find_segment_index_before_first():
    segs = [Segment(2.0, "a"), Segment(5.0, "b")]
    assert find_segment_index(segs, 0.0) == 0
    assert find_segment_index(segs, 1.9) == 0


def test_find_segment_index_exact_start():
    segs = [Segment(0.0, "a"), Segment(5.0, "b"), Segment(10.0, "c")]
    assert find_segment_index(segs, 0.0) == 0
    assert find_segment_index(segs, 5.0) == 1
    assert find_segment_index(segs, 10.0) == 2


def test_find_segment_index_midway():
    segs = [Segment(0.0, "a"), Segment(5.0, "b"), Segment(10.0, "c")]
    assert find_segment_index(segs, 2.5) == 0
    assert find_segment_index(segs, 7.0) == 1
    assert find_segment_index(segs, 9.999) == 1


def test_find_segment_index_past_last():
    segs = [Segment(0.0, "a"), Segment(5.0, "b")]
    assert find_segment_index(segs, 9999.0) == 1


def test_find_segment_index_empty_list():
    assert find_segment_index([], 0.0) == 0


def test_stub_play_stop():
    p = StubAudioPlayer()
    assert p.is_playing is False
    p.play(Path("/tmp/foo.mp3"))
    assert p.is_playing is True
    assert p.played_path == Path("/tmp/foo.mp3")
    assert p.time_pos == 0.0
    p.stop()
    assert p.is_playing is False
    assert p.stop_called is True
    assert p.time_pos is None


def test_stub_seek_relative_clamps_at_zero():
    p = StubAudioPlayer()
    p.play(Path("/tmp/x"))
    p.time_pos = 10.0
    p.seek_relative(-5.0)
    assert p.time_pos == 5.0
    p.seek_relative(-100.0)
    assert p.time_pos == 0.0
    assert p.relative_seeks == [-5.0, -100.0]


def test_stub_seek_relative_forward():
    p = StubAudioPlayer()
    p.play(Path("/tmp/x"))
    p.time_pos = 10.0
    p.seek_relative(5.0)
    assert p.time_pos == 15.0


def test_stub_seek_absolute():
    p = StubAudioPlayer()
    p.play(Path("/tmp/x"))
    p.seek_absolute(42.0)
    assert p.time_pos == 42.0
    assert p.absolute_seeks == [42.0]


def test_stub_seek_absolute_clamps_at_zero():
    p = StubAudioPlayer()
    p.play(Path("/tmp/x"))
    p.seek_absolute(-3.0)
    assert p.time_pos == 0.0
