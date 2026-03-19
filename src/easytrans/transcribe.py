"""Audio conversion and Whisper transcription for EasyTrans."""

import multiprocessing
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from easytrans.config import EasyTransConfig
from easytrans.files import audio_path, text_path, wav_path
from easytrans.models import Memo, Transcription


def convert_to_wav(source: Path, dest: Path) -> None:
    """Convert an audio file to 16kHz mono WAV for Whisper."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(source),
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def _whisper_worker(wav_file: str, model_name: str, result_queue: multiprocessing.Queue) -> None:
    """Run Whisper transcription in a child process."""
    # Prevent CTranslate2/CUDA from probing the GPU — can crash the NVIDIA driver
    # when the GPU is under load from the display server.
    os.environ["CUDA_VISIBLE_DEVICES"] = ""

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(wav_file, beam_size=1)

    result = []
    for segment in segments:
        result.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
        })
    result_queue.put(result)


def transcribe_audio(
    wav_file: Path,
    model_name: str,
    active_processes: set | None = None,
) -> list[dict]:
    """Transcribe a WAV file using faster-whisper in a child process.

    Runs Whisper in a separate process so it can be killed on shutdown.
    If active_processes is provided, the process is registered there
    while running so callers can kill it.
    """
    q = multiprocessing.Queue()
    p = multiprocessing.Process(
        target=_whisper_worker,
        args=(str(wav_file), model_name, q),
    )
    if active_processes is not None:
        active_processes.add(p)
    try:
        p.start()
        p.join()
        if p.exitcode != 0:
            raise RuntimeError(
                f"Transcription process exited with code {p.exitcode}"
            )
        return q.get_nowait()
    finally:
        if active_processes is not None:
            active_processes.discard(p)


def format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS."""
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes:02d}:{secs:02d}"


def segments_to_text(segments: list[dict], include_timestamps: bool = False) -> str:
    """Convert transcription segments to a text string."""
    if include_timestamps:
        lines = []
        for seg in segments:
            ts = format_timestamp(seg["start"])
            lines.append(f"[{ts}] {seg['text']}")
        return "\n".join(lines)
    else:
        return " ".join(seg["text"] for seg in segments)


def transcribe_memo(
    config: EasyTransConfig,
    session: Session,
    memo: Memo,
    model_name: str | None = None,
    overwrite_md: bool = True,
    active_processes: set | None = None,
) -> Transcription:
    """Convert and transcribe a single memo.

    Converts to WAV if needed, runs Whisper, stores the result
    in the database, and writes the .md file.
    """
    if model_name is None:
        model_name = config.whisper.default_model

    # Find the source audio file (non-wav)
    year = memo.file_id.split("-")[0]
    year_dir = config.audio_dir / year
    source = None
    for f in year_dir.iterdir():
        if f.stem == memo.file_id and f.suffix.lower() != ".wav":
            source = f
            break

    if source is None:
        raise FileNotFoundError(f"Source audio not found for {memo.file_id}")

    # Convert to WAV
    wav = wav_path(config.data_dir, memo.file_id)
    if not wav.exists():
        convert_to_wav(source, wav)

    # Transcribe
    segments = transcribe_audio(wav, model_name, active_processes=active_processes)

    # Store timestamped text in DB
    timestamped_text = segments_to_text(segments, include_timestamps=True)
    transcription = Transcription(
        memo_hash=memo.file_hash,
        transcribed_at=datetime.now(tz=timezone.utc),
        model_name=model_name,
        text=timestamped_text,
    )
    session.add(transcription)
    session.flush()

    # Write clean text to .md file
    if overwrite_md:
        md = text_path(config.data_dir, memo.file_id)
        md.parent.mkdir(parents=True, exist_ok=True)
        clean_text = segments_to_text(segments, include_timestamps=False)
        md.write_text(clean_text + "\n")

    return transcription


def transcribe_memos_parallel(
    config: EasyTransConfig,
    engine: Engine,
    memos: list[Memo],
    model_name: str | None = None,
    on_complete: Callable[[Memo], None] | None = None,
) -> list[Transcription]:
    """Transcribe multiple memos in parallel using a thread pool.

    Each thread gets its own DB session. Calls on_complete callback
    after each memo finishes (for UI progress updates).
    """
    results: list[Transcription] = []

    def _process(memo: Memo) -> Transcription:
        with Session(engine) as session:
            t = transcribe_memo(config, session, memo, model_name)
            session.commit()
            if on_complete:
                on_complete(memo)
            return t

    # Use 2 workers to avoid overwhelming CPU on a small machine
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_process, m) for m in memos]
        for future in futures:
            results.append(future.result())

    return results
