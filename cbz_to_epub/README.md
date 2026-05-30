# cbz-to-epub

Convert `.cbz` / `.cbr` comic archives to EPUB.

- Extracts images (`.jpg`, `.jpeg`, `.png`, `.webp` — case variants on Unix)
- Converts non-WEBP images to WEBP in-memory (ImageMagick) and keeps the smaller version
- WEBP sources are passed through unchanged (fast path, no re-encode)
- Builds a clean EPUB 3 with one full-page image per spread (SVG wrappers for scaling)
- Uses the local `rwt-epub` library for the EPUB generation

## Prerequisites

- Python 3.13+
- **7-Zip** (provides `7zz` on macOS/Linux, `7z` on Windows)
- **ImageMagick** (provides the `magick` command)

## Install (uv)

```bash
# From inside this directory (editable, with local rwt-epub dep)
uv tool install .

# Or run without permanent install
uv run cbz-to-epub --help

# Or from anywhere with uvx (after first use it is cached)
uvx --from /path/to/this/dir cbz-to-epub ...
```

After `uv tool install`, the command `cbz-to-epub` is on your PATH.

## Usage

```bash
cbz-to-epub --series "G.I. Joe" --inum 42 --iyear 1985 my-comic.cbz
```

- Output: `G.I. Joe 042.epub` in the current directory
- `--series` and `--publisher` (author) default to classic Marvel/G.I. Joe values
- `--inum` / `--iyear` default to `-1` (produces titles like `Series -01.epub`)

The tool leaves a `temp_out/` directory with the extracted images for inspection (clean it manually or via your own scripts).

## Notes

- Only JPEG/PNG/WEBP images inside the archive are processed (other files ignored).
- The first image (lexical sort after extraction) is registered as the cover.
- Pages and the TOC are sorted by the final optimized filename.
