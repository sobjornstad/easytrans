"""Tests for file ID generation and hashing."""

from pathlib import Path

from easytrans.files import (
    audio_path,
    compute_file_hash,
    find_source_audio,
    next_file_id,
    text_path,
    wav_path,
)


def test_compute_file_hash(tmp_path: Path) -> None:
    f = tmp_path / "test.mp3"
    f.write_bytes(b"fake audio data")
    h = compute_file_hash(f)
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex digest


def test_compute_file_hash_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "test.mp3"
    f.write_bytes(b"same content")
    assert compute_file_hash(f) == compute_file_hash(f)


def test_different_files_different_hashes(tmp_path: Path) -> None:
    f1 = tmp_path / "a.mp3"
    f2 = tmp_path / "b.mp3"
    f1.write_bytes(b"content a")
    f2.write_bytes(b"content b")
    assert compute_file_hash(f1) != compute_file_hash(f2)


def test_next_file_id_empty_dir(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    assert next_file_id(audio_dir, 2026) == "2026-0001"


def test_next_file_id_no_year_dir(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    assert next_file_id(audio_dir, 2026) == "2026-0001"


def test_next_file_id_existing_files(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    year_dir = audio_dir / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "2026-0001.mp3").touch()
    (year_dir / "2026-0002.mp3").touch()
    # Derivative WAV shares its stem with the source, so 0002 is counted
    # once — not twice — and the next slot is 0003.
    (year_dir / "2026-0002.wav").touch()
    assert next_file_id(audio_dir, 2026) == "2026-0003"


def test_next_file_id_wav_only_slot_taken(tmp_path: Path) -> None:
    """
    A WAV with no non-WAV sibling is a direct-recording memo — it
    occupies its slot, so the next id should be the one above it.
    """
    audio_dir = tmp_path / "audio"
    year_dir = audio_dir / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "2026-0001.wav").touch()
    assert next_file_id(audio_dir, 2026) == "2026-0002"


def test_next_file_id_mixed_wav_sources_and_derivatives(tmp_path: Path) -> None:
    """Regression: a synced .mp3 + derivative .wav and a WAV-only
    direct recording can coexist in the same year directory."""
    audio_dir = tmp_path / "audio"
    year_dir = audio_dir / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "2026-0001.mp3").touch()   # synced source
    (year_dir / "2026-0001.wav").touch()   # derivative for Whisper
    (year_dir / "2026-0002.wav").touch()   # direct recording (no sibling)
    assert next_file_id(audio_dir, 2026) == "2026-0003"


def test_audio_path() -> None:
    p = audio_path(Path("/data"), "2026-0001", ".mp3")
    assert p == Path("/data/audio/2026/2026-0001.mp3")


def test_wav_path() -> None:
    p = wav_path(Path("/data"), "2026-0001")
    assert p == Path("/data/audio/2026/2026-0001.wav")


def test_text_path() -> None:
    p = text_path(Path("/data"), "2026-0001")
    assert p == Path("/data/text/2026/2026-0001.md")


def test_find_source_audio_prefers_non_wav(tmp_path: Path) -> None:
    year_dir = tmp_path / "audio" / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "2026-0001.mp3").touch()
    (year_dir / "2026-0001.wav").touch()
    src = find_source_audio(tmp_path, "2026-0001")
    assert src is not None
    assert src.suffix == ".mp3"


def test_find_source_audio_falls_back_to_wav(tmp_path: Path) -> None:
    """Direct in-app recordings have only a WAV — that IS the source."""
    year_dir = tmp_path / "audio" / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "2026-0001.wav").touch()
    src = find_source_audio(tmp_path, "2026-0001")
    assert src is not None
    assert src.suffix == ".wav"


def test_find_source_audio_missing_returns_none(tmp_path: Path) -> None:
    (tmp_path / "audio" / "2026").mkdir(parents=True)
    assert find_source_audio(tmp_path, "2026-0001") is None
