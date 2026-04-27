# EasyTrans

A small terminal app for managing voice memo transcriptions. Sync recordings
from a USB voice recorder (or import any audio file, or record live in-app),
transcribe them locally with [faster-whisper], browse and edit the results
in a Textual TUI, and play back audio with synced highlighting of the
transcript.

[faster-whisper]: https://github.com/SYSTRAN/faster-whisper

This is a vibecoded personal-workflow tool; it's published in case the design
is useful to others. See [`spec/SPEC.md`](spec/SPEC.md) for the full product/UX
intent.

## Install and run

```sh
uv sync
uv run easytrans
```

On first launch, a default config is written to
`~/.config/easytrans/config.toml`. Edit it to point at your recorder's
device path, mount point, and audio directory, plus your preferred Whisper
model sizes.

Data (audio, transcript markdown files, SQLite DB) lives under `data_dir`
from the config — `~/easytrans-data` by default.

## Hardware caveat

`whisper.cpu_threads` in `config.toml` is intentionally capped well below
the host's core count. Sustained all-core AVX2 load from CTranslate2 has
been observed to hard-lock the development machine. **Do not raise this
without sustained-load testing, and do not set it to 0 ("all cores").**
Full write-up in [`spec/SPEC.md`](spec/SPEC.md) under "Known hardware
issue".

## Development

```sh
uv run pytest          # tests
uv run pyright         # type check
```

Architecture and design notes: [`CLAUDE.md`](CLAUDE.md),
[`spec/SPEC.md`](spec/SPEC.md), [`spec/VIM-NAVIGATION.md`](spec/VIM-NAVIGATION.md).

## License

MIT — see [LICENSE](LICENSE).
