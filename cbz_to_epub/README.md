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
- `--inum` accepts an integer (zero-padded to 3 digits, e.g. `42` → `042`) or any string used as-is (e.g. `--inum "Annual 1"`). If omitted, the title is just the series name (no trailing space).
- `--iyear` defaults to `-1`

The tool leaves a `temp_out/` directory with the extracted images for inspection (clean it manually or via your own scripts).

## Custom Table of Contents

By default the generated EPUB contains two TOC entries: "Cover" (page 1) and "Last Page".

You can supply a custom nested TOC using `--toc` (or `-t`):

```bash
cbz-to-epub --toc my-toc.json my-comic.cbz
```

When `--toc` is provided, the two automatic entries are **not** added.

### TOC File Format (JSON)

```json
[
  { "title": "Chapter 1: The Beginning", "page": 1, "level": 1 },
  { "title": "The Village", "page": 3, "level": 2 },
  { "title": "Chapter 2: The Road", "page": 12, "level": 1 },
  { "title": "Epilogue", "page": 47, "level": 1 }
]
```

- `title`: Chapter/section heading (required)
- `page`: 1-based page number in the final book (required)
- `level`: Nesting level 1–3 (optional, defaults to 1)

The same format is also shown at the bottom of `cbz-to-epub --help`.

## Notes

- Only JPEG/PNG/WEBP images inside the archive are processed (other files ignored).
- The first image (lexical sort after extraction) is registered as the cover.
- Internal files are always named `page-001.*`, `page-002.*`, etc. (zero-padded based on total page count) in the order the original filenames sorted. This makes the EPUB contents predictable and easy to inspect.
