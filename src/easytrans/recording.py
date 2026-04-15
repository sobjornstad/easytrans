"""
Live audio recording for EasyTrans.

`Recorder` wraps a `sounddevice.InputStream` writing straight into a
`soundfile.SoundFile` so in-progress frames hit disk continuously. The
recorder knows nothing about memos — it just produces a WAV file at a
staging path. The caller (`app._do_record`) is responsible for
promoting that file into a memo via `import_audio_as_memo`.

Design notes:
- Writing happens inside the PortAudio callback thread. SoundFile.write
  is safe to call from one writer thread; we only ever write from the
  callback.
- Elapsed time is derived from a wall clock captured at start(), not
  from frame count, so the UI timer keeps ticking even if the audio
  device drops frames.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import sounddevice as sd
import soundfile as sf

from easytrans.config import EasyTransConfig


class Recorder:
    """
    One-shot live recorder. Instantiate, `start()`, `stop()` or `cancel()`.

    Recordings are 16-bit mono PCM WAV at the configured sample rate
    (16 kHz by default — Whisper's native rate, keeps files small).
    """

    def __init__(self, config: EasyTransConfig) -> None:
        self._config = config
        self._samplerate = config.recording.samplerate
        self._device = config.recording.device
        self._path: Path | None = None
        self._file: sf.SoundFile | None = None
        self._stream: sd.InputStream | None = None
        self._start_monotonic: float | None = None
        self._stopped: bool = False

    @property
    def elapsed_seconds(self) -> float:
        "Seconds since start(), or 0.0 before start is called."
        if self._start_monotonic is None:
            return 0.0
        return time.monotonic() - self._start_monotonic

    def start(self) -> Path:
        """
        Open the staging WAV file and begin capturing from the input device.

        Returns the staging path so tests can assert on it.
        """
        assert self._stream is None, "Recorder already started"
        self._config.ensure_dirs()
        path = self._config.recording_tmp_dir / f"rec-{uuid.uuid4().hex}.wav"
        self._path = path
        self._file = sf.SoundFile(
            str(path),
            mode="w",
            samplerate=self._samplerate,
            channels=1,
            subtype="PCM_16",
        )

        def callback(indata, frames, time_info, status) -> None:
            # `status` flags underruns/overflows; we ignore them — the
            # recording continues and the user hears no glitch since
            # nothing is being played back. If this becomes a problem
            # we can surface it via a warning later.
            if self._file is not None:
                self._file.write(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self._samplerate,
            channels=1,
            dtype="int16",
            device=self._device,
            callback=callback,
        )
        self._stream.start()
        self._start_monotonic = time.monotonic()
        return path

    def stop(self) -> Path:
        """
        Stop the stream, finalize the WAV file, and return its path.

        Idempotent: calling stop() twice returns the same path without
        re-closing the already-closed stream.
        """
        if self._stopped:
            assert self._path is not None
            return self._path
        self._stopped = True
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._file is not None:
            self._file.close()
            self._file = None
        assert self._path is not None
        return self._path

    def cancel(self) -> None:
        """
        Stop the stream and delete the staging file.

        After cancel() the recorder cannot be reused.
        """
        path = self.stop()
        path.unlink(missing_ok=True)
