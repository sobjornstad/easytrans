"""Tests for file ID generation and hashing."""

from pathlib import Path

from easytrans.files import compute_file_hash, next_file_id, audio_path, text_path, wav_path


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
    (year_dir / "2026-0002.wav").touch()  # WAV should be ignored
    assert next_file_id(audio_dir, 2026) == "2026-0003"


def test_next_file_id_skips_wav_only(tmp_path: Path) -> None:
    """WAV files alone shouldn't count toward numbering."""
    audio_dir = tmp_path / "audio"
    year_dir = audio_dir / "2026"
    year_dir.mkdir(parents=True)
    (year_dir / "2026-0001.wav").touch()
    # Only a .wav exists, no source - still counts as 0001 used?
    # Actually no - the spec says .wav is excluded from counting.
    assert next_file_id(audio_dir, 2026) == "2026-0001"


def test_audio_path() -> None:
    p = audio_path(Path("/data"), "2026-0001", ".mp3")
    assert p == Path("/data/audio/2026/2026-0001.mp3")


def test_wav_path() -> None:
    p = wav_path(Path("/data"), "2026-0001")
    assert p == Path("/data/audio/2026/2026-0001.wav")


def test_text_path() -> None:
    p = text_path(Path("/data"), "2026-0001")
    assert p == Path("/data/text/2026/2026-0001.md")
