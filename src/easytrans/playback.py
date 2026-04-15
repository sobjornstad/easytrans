"""Audio playback support for EasyTrans.

Provides a thin AudioPlayer interface around python-mpv so the TUI can
play, seek, and poll position, plus helpers for parsing the timestamped
transcript format stored in the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Segment:
    """A single transcript segment with its start time in seconds."""
    start: float
    text: str


_SEGMENT_RE = re.compile(r"^\[(\d{1,2}):(\d{2})\]\s*(.*)$")


def parse_segments(db_text: str) -> list[Segment]:
    """Parse '[MM:SS] text' lines from a stored transcription into segments.

    Lines that do not match the timestamp prefix are silently skipped.
    """
    segments: list[Segment] = []
    for line in db_text.splitlines():
        m = _SEGMENT_RE.match(line)
        if not m:
            continue
        minutes = int(m.group(1))
        seconds = int(m.group(2))
        segments.append(Segment(start=float(minutes * 60 + seconds), text=m.group(3)))
    return segments


def find_segment_index(segments: list[Segment], t: float) -> int:
    """Return the index of the segment whose [start, next_start) contains t.

    For t before the first segment, returns 0. For t past the last segment,
    returns the last index. Returns 0 for an empty list (callers should
    guard against that case).
    """
    if not segments:
        return 0
    if t < segments[0].start:
        return 0
    # Linear scan is fine — transcripts are short.
    for i in range(len(segments) - 1):
        if segments[i].start <= t < segments[i + 1].start:
            return i
    return len(segments) - 1


class AudioPlayer(Protocol):
    """Minimal interface the TUI needs from an audio backend."""

    def play(self, path: Path) -> None: ...
    def stop(self) -> None: ...
    def seek_relative(self, delta: float) -> None: ...
    def seek_absolute(self, t: float) -> None: ...

    @property
    def time_pos(self) -> float | None: ...

    @property
    def duration(self) -> float | None: ...


class MpvAudioPlayer:
    """Real AudioPlayer backed by libmpv via python-mpv."""

    def __init__(self) -> None:
        import mpv
        self._mpv = mpv.MPV(
            video="no",
            audio_display="no",
            ytdl=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            keep_open=False,
        )
        self._stopped = False

    def play(self, path: Path) -> None:
        self._mpv.play(str(path))

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self._mpv.command("stop")
        except Exception:
            pass
        try:
            self._mpv.terminate()
        except Exception:
            pass

    def seek_relative(self, delta: float) -> None:
        try:
            self._mpv.seek(delta, reference="relative")
        except Exception:
            pass

    def seek_absolute(self, t: float) -> None:
        try:
            self._mpv.seek(max(0.0, t), reference="absolute")
        except Exception:
            pass

    @property
    def time_pos(self) -> float | None:
        try:
            v = self._mpv.time_pos
        except Exception:
            return None
        return float(v) if v is not None else None

    @property
    def duration(self) -> float | None:
        try:
            v = self._mpv.duration
        except Exception:
            return None
        return float(v) if v is not None else None


class StubAudioPlayer:
    """In-memory AudioPlayer for tests.

    Tracks calls and exposes ``time_pos``/``duration`` as plain attributes
    so tests can advance the playhead manually.
    """

    def __init__(self, duration: float = 60.0) -> None:
        self.played_path: Path | None = None
        self.is_playing: bool = False
        self.time_pos: float | None = None
        self.duration: float | None = duration
        self.relative_seeks: list[float] = []
        self.absolute_seeks: list[float] = []
        self.stop_called: bool = False

    def play(self, path: Path) -> None:
        self.played_path = path
        self.is_playing = True
        self.time_pos = 0.0

    def stop(self) -> None:
        self.is_playing = False
        self.stop_called = True
        self.time_pos = None

    def seek_relative(self, delta: float) -> None:
        self.relative_seeks.append(delta)
        if self.time_pos is not None:
            self.time_pos = max(0.0, self.time_pos + delta)

    def seek_absolute(self, t: float) -> None:
        self.absolute_seeks.append(t)
        self.time_pos = max(0.0, t)
