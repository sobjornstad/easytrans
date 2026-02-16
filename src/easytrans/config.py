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
default_model = "tiny"
# Model for re-transcription with higher quality
large_model = "medium"
"""


@dataclass
class RecorderConfig:
    device_path: str = "/dev/sda1"
    mount_point: str = "/media/vok"
    audio_dir: str = "RECORDER/FOLDER_B"


@dataclass
class WhisperConfig:
    default_model: str = "tiny"
    large_model: str = "medium"


@dataclass
class EasyTransConfig:
    data_dir: Path = field(default_factory=lambda: Path.home() / "easytrans-data")
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "easytrans.db"

    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.text_dir.mkdir(parents=True, exist_ok=True)


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
            default_model=whisper.get("default_model", "tiny"),
            large_model=whisper.get("large_model", "medium"),
        ),
    )
