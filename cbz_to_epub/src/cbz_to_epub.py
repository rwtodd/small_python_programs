#!/usr/bin/env python3
"""cbz-to-epub: Convert CBZ/CBR comic archives to EPUB.

Extracts images (jpg/jpeg/png/webp), optionally converts to WEBP when smaller,
and builds a clean EPUB using rwt_epub.EpubWriter (one full-page image per spread).

Optional --toc can supply a custom nested Table of Contents (JSON) using 1-based page numbers.
When a TOC file is provided, the automatic "Cover" and "Last Page" entries are suppressed.
"""

import concurrent.futures
import os
import re
import sys
import subprocess
import textwrap
from pathlib import Path
from typing import Any
import argparse
from rwt_epub import EpubWriter

# --- Configuration ---
WEBP_QUALITY = '80%'
TEMP_OUT = 'temp_out'
SVN7_PATH = r'C:\Program Files\7-Zip\7z.exe' if os.name == 'nt' else '7zz'


def parse_inum(value: str) -> str:
    """Parse --inum: integers are zero-padded to 3 digits; other strings pass through as-is."""
    try:
        return f"{int(value):03d}"
    except ValueError:
        return value


def _numeric_stem(path: Path) -> int | None:
    """Return the integer page number if the filename stem is purely numeric.

    Accepts plain names (``1``, ``001``, ``42``) and AppleDouble resource-fork
    sidecars produced by flat extracts (``._1``, ``._001``). Returns None for
    any other stem.
    """
    stem = path.stem
    if stem.startswith("._"):
        stem = stem[2:]
    if stem.isdigit():
        return int(stem)
    return None


def images_use_numeric_names(images: list[Path]) -> bool:
    """True when every image looks like a pure numeric page name.

    Many CBZ/CBR archives name pages ``0.jpg``, ``1.jpg``, … ``10.jpg`` with
    no zero-padding and expect readers to order them numerically (not
    lexicographically). Detection ignores AppleDouble ``._N`` sidecars so a
    flat 7-Zip extract still counts as numeric when the real pages are.
    """
    if not images:
        return False
    return all(_numeric_stem(p) is not None for p in images)


def natural_sort_key(path: Path) -> list:
    """Natural (alphanumeric) sort key: digit runs compared as integers.

    Used when names are not purely numeric but still embed page numbers
    (e.g. ``page-2.jpg`` vs ``page-10.jpg``).
    """
    parts = re.split(r"(\d+)", path.name)
    key: list = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.casefold())
    return key


def sort_images(images: list[Path]) -> None:
    """Sort image paths into reading order, in place.

    - Pure numeric basenames (``1.jpg``, ``10.jpg``, optional ``._N`` junk):
      integer order, matching common comic-reader conventions.
    - Otherwise: natural alphanumeric order so embedded numbers still sort
      correctly (``page2`` before ``page10``).
    """
    if images_use_numeric_names(images):
        # All stems are numeric after detection; 0 is only a type-narrowing fallback.
        images.sort(key=lambda p: _numeric_stem(p) or 0)
    else:
        images.sort(key=natural_sort_key)

def process_image(img_path: Path) -> tuple[str, tuple[int, int], bytes] | None:
    """
    For a source image (jpg/jpeg/png/webp), optionally convert to WEBP (when
    smaller), and return the best (extension, dims, data) tuple.

    - .webp inputs are passed through unchanged (no pointless re-encode).
    - jpg/jpeg/png inputs are converted to WEBP in memory; the smaller of the
      two versions is kept (webp wins only when strictly smaller).
    - Original extension is preserved for non-webp "keep original" cases
      (jpeg normalized to .jpg for consistency with prior behavior).
    - If ImageMagick cannot identify the file (corrupt / not a real image),
      log a warning and return None so the caller skips it entirely.

    The caller is responsible for assigning the final base name (we now use
    deterministic page-NNN names regardless of the original filename).

    Args:
        img_path: Path to the input image in the temp extraction dir.

    Returns:
        (ext_with_dot, (w, h), data_bytes) on success, e.g.
        (".webp", (800, 1200), b'...'), or None if the file should be skipped.
    """
    suffix = img_path.suffix.lower()
    orig_ext = suffix.lstrip(".")
    if orig_ext == "jpeg":
        orig_ext = "jpg"  # normalize for output names and magick format hint
    data = img_path.read_bytes()
    is_already_webp = (suffix == ".webp")

    # Identify dimensions first — failure means the file is not a usable image.
    fmt_hint = orig_ext if not is_already_webp else "webp"
    try:
        result = subprocess.run(
            ["magick", "identify", "-format", "%w %h", f"{fmt_hint}:-"],
            input=data,
            capture_output=True,
            check=True,
        )
        w, h = [int(x) for x in result.stdout.split()]
    except subprocess.CalledProcessError as e:
        print(
            f"⚠️  Could not identify '{img_path.name}'. Skipping. Reason: {e.stderr.decode().strip()}",
            file=sys.stderr,
        )
        return None

    if is_already_webp:
        # Fast path: keep as-is, never re-encode
        return ".webp", (w, h), data

    # Non-webp source: try WEBP conversion and keep whichever is smaller
    try:
        result = subprocess.run(
            ["magick", "convert", "-quality", WEBP_QUALITY, f"{orig_ext}:-", "webp:-"],
            input=data,
            capture_output=True,
            check=True,
        )
        webp_data = result.stdout

        if len(webp_data) > 0 and len(webp_data) < len(data):
            return ".webp", (w, h), webp_data
        else:
            return f".{orig_ext}", (w, h), data

    except subprocess.CalledProcessError as e:
        print(
            f"⚠️  Could not convert '{img_path.name}' to WEBP. Using original. Reason: {e.stderr.decode().strip()}",
            file=sys.stderr,
        )
        return f".{orig_ext or 'jpg'}", (w, h), data

def create_optimized_zip(
    source_archive: Path,
    metadata: dict[str, Any],
    output_file: Path,
    toc_path: Path | None = None,
    language: str = "en",
) -> None:
    """
    Finds all images (jpg/jpeg/png/webp) in the extracted archive, processes
    them (WEBP passthrough or size-comparison conversion for others), and
    builds the EPUB via EpubWriter (one image per page using SVG wrappers).

    Images and XHTML pages are given clean deterministic names of the form
    page-001.webp / page-001.xhtml (zero-padded, based on position after
    sorting the original filenames).

    Args:
        source_archive: The .cbz or .cbr file.
        metadata: dict with 'title', 'author', 'year'
        output_file: desired .epub path.
        toc_path: Optional path to a JSON TOC file. When provided, custom
                  TOC entries are used and the automatic Cover/Last-Page
                  entries are suppressed.
        language: Language code for <dc:language> in the EPUB (defaults to "en").
    """
    # 1. expand the comic archive (flat extract)
    src_path = Path(TEMP_OUT)
    try:
        subprocess.run(
            [SVN7_PATH, 'e', f'-o{TEMP_OUT}', str(source_archive)],
            check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ Error: could not expand the comic!", file=sys.stderr)
        return

    # 2. Find supported images (case variants only on non-Windows for now)
    exts = ['*.jpg', '*.jpeg', '*.png', '*.webp']
    if os.name != 'nt':
        exts += ['*.JPG', '*.JPEG', '*.PNG', '*.WEBP']
    images: list[Path] = []
    for pat in exts:
        images.extend(src_path.glob(pat))
    if not images:
        print(f"🤷 No supported images (.jpg/.jpeg/.png/.webp) found in '{src_path}'.", file=sys.stderr)
        return
    sort_images(images)  # numeric or natural order → canonical reading order
    total = len(images)
    width = max(3, len(str(total)))  # e.g. page-001, or page-0001 for very large books
    max_processes = os.cpu_count() or 4
    print(f"⚙️  Found {total} images. Starting processing with up to {max_processes} parallel workers.")

    # 3. Process images (preserving sorted order). Identify failures return None
    #    and are skipped entirely — treated as if the file was never present.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_processes) as executor:
            # executor.map preserves the order of the input (images list)
            processed = list(executor.map(process_image, images))

        pages: list[tuple[str, tuple[int, int], bytes]] = []
        for result in processed:
            if result is None:
                continue
            pages.append(result)

        if not pages:
            print("❌ No usable images after processing (all failed identify).", file=sys.stderr)
            return

        # 4. Assign clean page-NNN names and write EPUB
        with EpubWriter(str(output_file), metadata['title'], metadata['author'], metadata['year'], language=language) as ew:
            image_files: list[str] = []

            for page_num, (ext, dims, data) in enumerate(pages, start=1):
                page_name = f"page-{page_num:0{width}d}{ext}"
                is_cover = (page_num == 1)  # first successfully processed image
                print(f"Add image content: {page_name}")
                ew.add_image_content(page_name, data, is_cover, dims)
                image_files.append(page_name)

            # Write xhtml pages in the same order (already correct, no extra sort needed)
            for ifile in image_files:
                xhtml_name = str(Path(ifile).with_suffix(".xhtml"))
                print(f"Add fullpage pic: {xhtml_name}")
                ew.add_fullpage_pic(xhtml_name, ifile)

            # TOC handling: custom file (if provided) or historical defaults.
            # We use integer page numbers (1-based spine positions) for both paths.
            num_pages = len(image_files)
            toc = load_toc_file(toc_path, num_pages) if toc_path else None

            if toc:
                for entry in toc:
                    print(f'Add TOC entry (p.{entry["page"]}, level {entry["level"]}): {entry["title"]}')
                    ew.add_toc_entry(entry["title"], entry["page"], level=entry["level"])
            else:
                # Historical default behavior (now using integer targets for consistency)
                ew.add_toc_entry("Cover", 1)
                ew.add_toc_entry("Last Page", num_pages)
            print('Done with epub file')
        print(f"\n🎉 Success! Created optimized archive at: {output_file}")

    except Exception as exc:
        print(f"An unexpected error occurred: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exception(exc)

def prepare_out_dir(odir: Path) -> None:
    """Empty the out dir, or create it if necessary"""
    if odir.exists():
        for file in odir.iterdir():
            if file.is_file():  # Check if it's a file
                file.unlink()  # Remove the file
    else:
        odir.mkdir()


def load_toc_file(path: Path, num_pages: int) -> list[dict]:
    """Load and validate a custom TOC JSON file.

    Returns a list suitable for direct use with add_toc_entry using integer targets.
    Exits with a clear error message on any validation failure.
    """
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ TOC file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in TOC file {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print("❌ TOC file must contain a JSON array of entries", file=sys.stderr)
        sys.exit(1)

    toc: list[dict] = []
    for i, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            print(f"❌ TOC entry #{i} must be an object", file=sys.stderr)
            sys.exit(1)
        if "title" not in entry or "page" not in entry:
            print(f"❌ TOC entry #{i} must have 'title' and 'page' keys", file=sys.stderr)
            sys.exit(1)

        title = entry["title"]
        page = entry["page"]
        level = entry.get("level", 1)

        if not isinstance(title, str) or not title.strip():
            print(f"❌ TOC entry #{i}: 'title' must be a non-empty string", file=sys.stderr)
            sys.exit(1)
        if not isinstance(page, int) or page < 1:
            print(f"❌ TOC entry #{i}: 'page' must be an integer >= 1", file=sys.stderr)
            sys.exit(1)
        if page > num_pages:
            print(
                f"❌ TOC entry #{i}: page {page} is out of range (book has only {num_pages} pages)",
                file=sys.stderr,
            )
            sys.exit(1)
        if not isinstance(level, int) or not (1 <= level <= 3):
            print(f"❌ TOC entry #{i}: 'level' must be 1, 2, or 3", file=sys.stderr)
            sys.exit(1)

        toc.append({"title": title.strip(), "page": page, "level": level})

    # Validate level monotonicity (no jumps > +1) — matches rwt_epub library rules
    prev_level = 0
    for i, e in enumerate(toc, 1):
        if e["level"] > prev_level + 1:
            print(
                f"❌ TOC entry #{i}: level jumped from {prev_level} to {e['level']} (max jump is +1)",
                file=sys.stderr,
            )
            sys.exit(1)
        prev_level = e["level"]

    return toc


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cbz-to-epub",
        description="Convert a .cbz/.cbr file to .epub (images optimized to WEBP when beneficial)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("comic", type=Path, help="The comic archive to convert")
    parser.add_argument("--series", default="G.I. Joe", help="The name of comic series")
    parser.add_argument("--publisher", default="Marvel", help="The publisher of the comic")
    parser.add_argument(
        "--inum",
        type=parse_inum,
        default=None,
        help="Issue number: an integer (zero-padded to 3 digits) or any string used as-is. "
             "If omitted, the title is just the series name.",
    )
    parser.add_argument("--iyear", type=int, default=-1, help="The issue year of publication")
    parser.add_argument("--language", "-l", default="en",
                        help="Language code for the EPUB metadata (e.g. 'ja' or 'ja-JP' for Japanese comics)")
    parser.add_argument(
        "--toc",
        "-t",
        type=Path,
        default=None,
        help="Optional JSON file describing a custom nested TOC (suppresses the default Cover/Last-Page entries)",
    )
    parser.epilog = textwrap.dedent("""
        TOC file format (JSON):

          [
            {"title": "Chapter 1", "page": 1, "level": 1},
            {"title": "Section A", "page": 4, "level": 2},
            {"title": "Chapter 2", "page": 12}
          ]

        "page" is 1-based. "level" is 1-3 (default 1). When --toc is given, the automatic
        "Cover" and "Last Page" entries are not added.
    """).strip()

    args = parser.parse_args()

    odir = Path("temp_out")
    prepare_out_dir(odir)
    title = f"{args.series} {args.inum}" if args.inum is not None else args.series
    result_epub = Path(f"{title}.epub")
    if result_epub.exists():
        print("outfile already exists!", file=sys.stderr)
    else:
        metadata = {"title": title, "year": args.iyear, "author": args.publisher}
        create_optimized_zip(args.comic, metadata, result_epub, toc_path=args.toc, language=args.language)


if __name__ == "__main__":
    main()
