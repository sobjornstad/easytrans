"""Configuration loading for EasyTrans."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "easytrans" / "config.toml"

DEFAULT_CONFIG_CONTENT = """\
# EasyTrans configuration

[paths]
# Where transcriptions are stored (audio/ and text/ subdirectories)
data_dir = "~/easytrans-data"

[recorder]
# Block device path for the voice recorder
device_path = "/dev/sda1"
# Where to mount the recorder
mount_point = "/media/vok"
# Directory on the recorder containing audio files
audio_dir = "RECORDER/FOLDER_B"

[whisper]
# Model for initial fast transcription
initial_model = "tiny"
# If set, auto-upgrade all items to this model once initial fast transcriptions are done
default_model = "small"
# Model for re-transcription with higher quality
large_model = "medium"
# Maximum CPU threads per Whisper inference process.
# Keep this well below the number of cores to avoid overloading the machine:
# sustained all-core AVX2 load from CTranslate2 has been observed to hard-lock
# this system (see the "Known hardware issue" note in SPEC.md). Do not set to 0
# (which would mean "use all cores").
cpu_threads = 4
"""


@dataclass
class RecorderConfig:
    device_path: str = "/dev/sda1"
    mount_point: str = "/media/vok"
    audio_dir: str = "RECORDER/FOLDER_B"


@dataclass
class WhisperConfig:
    initial_model: str = "tiny"
    default_model: str = "small"
    large_model: str = "medium"
    cpu_threads: int = 4


@dataclass
class RecordingConfig:
    # Input device for sounddevice. None = system default; a string
    # matches by substring, an int selects by portaudio index.
    device: str | int | None = None
    # 16 kHz is what Whisper resamples to anyway; keeps files small.
    samplerate: int = 16000


@dataclass
class EasyTransConfig:
    data_dir: Path = field(default_factory=lambda: Path.home() / "easytrans-data")
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "easytrans.db"

    @property
    def recording_tmp_dir(self) -> Path:
        "Staging directory for in-progress recordings before they become memos."
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.text_dir.mkdir(parents=True, exist_ok=True)
        self.recording_tmp_dir.mkdir(parents=True, exist_ok=True)


def load_config(config_path: Path | None = None) -> EasyTransConfig:
    """Load configuration from TOML file, creating defaults if missing."""
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_CONTENT)
        return EasyTransConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    paths = data.get("paths", {})
    recorder = data.get("recorder", {})
    whisper = data.get("whisper", {})
    recording = data.get("recording", {})

    data_dir_str = paths.get("data_dir", "~/easytrans-data")
    data_dir = Path(data_dir_str).expanduser()

    return EasyTransConfig(
        data_dir=data_dir,
        recorder=RecorderConfig(
            device_path=recorder.get("device_path", "/dev/sdb1"),
            mount_point=recorder.get("mount_point", "/mnt/recorder"),
            audio_dir=recorder.get("audio_dir", "RECORDER/FOLDER_B"),
        ),
        whisper=WhisperConfig(
            initial_model=whisper.get("initial_model", "tiny"),
            default_model=whisper.get("default_model", "small"),
            large_model=whisper.get("large_model", "medium"),
            cpu_threads=whisper.get("cpu_threads", 4),
        ),
        recording=RecordingConfig(
            device=recording.get("device"),
            samplerate=recording.get("samplerate", 16000),
        ),
    )
