# zip-to-aac

Convert audio files inside ZIP archives to AAC (`.m4a`) using ffmpeg, with cover art embedding. A Python reimplementation of the Go version in `go-version/`.

## Features

- Opens ZIP archives and extracts music files (`.flac`, `.m4a`, `.mp3`, `.ogg`)
- Converts non-MP3/AAC files to AAC (256k CVBR, 44.1kHz, Apple aac_at encoder)
- Copies already-valid MP3/AAC files as-is (or embeds cover if missing)
- Finds cover art (`.jpg`/`.jpeg`, preferring names starting with `cover`, `Cover`, `folder`, or `Folder`)
- If a chosen cover is >600px in either dimension **or** >150 KB, automatically downscales it to a 600x600 JPEG (quality 75%) using ImageMagick **only when the result is smaller**
- Embeds cover art as attached picture on output files
- Processes up to 8 files in parallel using threads + subprocess
- Output directory named after the ZIP (sans `.zip`), files flattened to it
- Pure Python stdlib (argparse, subprocess, zipfile, json, concurrent.futures, etc.)
- MIT licensed

## Requirements

- Python 3.14.5+
- `ffmpeg` and `ffprobe` in PATH (with `aac_at` encoder support, e.g. ffmpeg-full on macOS)
- The `aresample` filter with soxr (usually included)
- `magick` (ImageMagick 7+) in PATH is **recommended** for automatic downscaling of oversized cover art (optional; the tool gracefully falls back when it is missing)

## Installation

```bash
# From this directory
uv tool install .

# Then run from anywhere
zip-to-aac album.zip
zip-to-aac *.zip
```

To uninstall:

```bash
uv tool uninstall zip-to-aac
```

## Usage

```
zip-to-aac path/to/album.zip [path/to/another.zip ...]
```

- For each ZIP, creates `./<basename>/` (e.g. `album.zip` → `./album/`)
- Converted/copied tracks land directly in the output dir (no subdirs)
- Progress printed per-track as they complete
- Warnings for missing cover art are printed to stderr
- Non-zero exit if any ZIP fails

## How it decides what to do

For each audio file (after ffprobe):

- If MP3 with mp3 codec **or** M4A with aac codec:
  - Has embedded cover (or no cover available)? → copy file as-is
  - Else → re-encode just to embed the cover (stream copy + attach pic)
- Otherwise (FLAC, OGG, etc. or wrong-codec m4a/mp3):
  - Convert to AAC `.m4a`
  - Embed cover if found

**Cover selection**: JPEGs whose zip-member name starts with `cover`, `Cover`, `folder`, or `Folder` are preferred. The last preferred one wins.

**Large cover handling**: Before embedding, if the selected JPEG is larger than 600 px in either dimension or >150 KB on disk, the tool will try to create a 600×600 q75 version using `magick`. The downsized file is only used if it is actually smaller than the original.

The conversion filter chain matches the original Go tool (soxr resample, aac_at CVBR 256k, faststart, etc.).

## Development

```bash
uv sync   # (no deps, but sets env)
uv run zip-to-aac --help
# or for testing changes:
python -m zip_to_aac --help   # may need PYTHONPATH=src
```

## Notes / Limitations

- Only JPEG covers are supported (matching original).
- Only the 4 listed audio extensions are processed; everything else in the ZIP is ignored.
- Output filenames are flattened (original subdirectories discarded).
- aac_at is Apple/macOS specific; on other platforms you may need to patch the codec args to `aac` + `libfdk_aac` or similar.
- No external Python dependencies by design.
- Cover resizing requires ImageMagick (`magick` + `identify`). When it is absent (or fails), large covers are embedded as-is.

## License

MIT — see [LICENSE](LICENSE).
