#!/usr/bin/env python3
"""cbz-to-epub: Convert CBZ/CBR comic archives to EPUB.

Extracts images (jpg/jpeg/png/webp), optionally converts to WEBP when smaller,
and builds a clean EPUB using rwt_epub.EpubWriter (one full-page image per spread).
"""

import concurrent.futures
import os
import sys
import subprocess
import re
from pathlib import Path
from typing import Any
import argparse
from rwt_epub import EpubWriter

# --- Configuration ---
WEBP_QUALITY = '80%'
TEMP_OUT = 'temp_out'
SVN7_PATH = r'C:\Program Files\7-Zip\7z.exe' if os.name == 'nt' else '7zz'

def process_image(img_path: Path) -> tuple[str, tuple[int, int], bytes]:
    """
    For a source image (jpg/jpeg/png/webp), optionally convert to WEBP (when
    smaller), and return the best (filename, dims, data) tuple.

    - .webp inputs are passed through unchanged (no pointless re-encode).
    - jpg/jpeg/png inputs are converted to WEBP in memory; the smaller of the
      two versions is kept (webp wins only when strictly smaller).
    - Original extension is preserved for non-webp "keep original" cases
      (jpeg normalized to .jpg for consistency with prior behavior).

    Args:
        img_path: Path to the input image in the temp extraction dir.

    Returns:
        (chosen_filename, (w, h), data_bytes)
    """
    stem = img_path.stem
    suffix = img_path.suffix.lower()
    orig_ext = suffix.lstrip(".")
    if orig_ext == "jpeg":
        orig_ext = "jpg"  # normalize for output names and magick format hint
    data = img_path.read_bytes()
    is_already_webp = (suffix == ".webp")

    try:
        # Identify dimensions (magick understands the format hint)
        fmt_hint = orig_ext if not is_already_webp else "webp"
        result = subprocess.run(
            ["magick", "identify", "-format", "%w %h", f"{fmt_hint}:-"],
            input=data,
            capture_output=True,
            check=True,
        )
        w, h = [int(x) for x in result.stdout.split()]

        if is_already_webp:
            # Fast path: keep as-is, never re-encode
            return f"{stem}.webp", (w, h), data

        # Non-webp source: try WEBP conversion and keep whichever is smaller
        result = subprocess.run(
            ["magick", "convert", "-quality", WEBP_QUALITY, f"{orig_ext}:-", "webp:-"],
            input=data,
            capture_output=True,
            check=True,
        )
        webp_data = result.stdout

        if len(webp_data) > 0 and len(webp_data) < len(data):
            return f"{stem}.webp", (w, h), webp_data
        else:
            return f"{stem}.{orig_ext}", (w, h), data

    except subprocess.CalledProcessError as e:
        print(
            f"⚠️  Could not process '{img_path.name}'. Using original. Reason: {e.stderr.decode().strip()}",
            file=sys.stderr,
        )
        return f"{stem}.{orig_ext or 'jpg'}", (0, 0), data

def create_optimized_zip(
    source_archive: Path,
    metadata: dict[str,Any],
    output_file: Path
) -> None:
    """
    Finds all images (jpg/jpeg/png/webp) in the extracted archive, processes
    them (WEBP passthrough or size-comparison conversion for others), and
    builds the EPUB via EpubWriter (one image per page using SVG wrappers).

    Args:
        source_archive: The .cbz or .cbr file.
        metadata: dict with 'title', 'author', 'year'
        output_file: desired .epub path.
    """
    nonalph = re.compile(r'[^a-zA-Z0-9.]')
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
    images.sort()  # first lexical becomes the cover candidate (same as before)
    cover_img_file = images[0]

    max_processes = os.cpu_count() or 4
    print(f"⚙️  Found {len(images)} images. Starting processing with up to {max_processes} parallel workers.")

    # 3. Process in parallel, add images + xhtml pages, then TOC links
    try:
        seen = set()
        with EpubWriter(str(output_file), metadata['title'], metadata['author'], metadata['year']) as ew:
            image_files: list[str] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_processes) as executor:
                future_to_path = {
                    executor.submit(process_image, p): p for p in images
                }
                for future in concurrent.futures.as_completed(future_to_path):
                    original_path = future_to_path[future]
                    try:
                        filename, dims, data = future.result()
                        if filename in seen:
                            print('We have ', filename, ' already!', file=sys.stderr)
                        elif filename.startswith('zzz'):
                            print('Skipping', filename, file=sys.stderr)
                        else:
                            seen.add(filename)
                            allalpha = nonalph.sub('', filename)
                            print('Add image content:', allalpha)
                            ew.add_image_content(allalpha, data, original_path == cover_img_file, dims)
                            image_files.append(allalpha)
                    except Exception as exc:
                        print(f"❌ Failed to process '{original_path.name}': {exc}", file=sys.stderr)

            # write xhtml pages (sorted for deterministic spine order)
            image_files.sort()
            for ifile in image_files:
                print('Add fullpage pic:', ifile)
                ew.add_fullpage_pic(str(Path(ifile).with_suffix('.xhtml')), ifile)

            # TOC entries (new API: text first, then target filename)
            ew.add_toc_entry('Cover', str(Path(image_files[0]).with_suffix('.xhtml')))
            ew.add_toc_entry('Last Page', str(Path(image_files[-1]).with_suffix('.xhtml')))
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

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cbz-to-epub",
        description="Convert a .cbz/.cbr file to .epub (images optimized to WEBP when beneficial)",
    )
    parser.add_argument("comic", type=Path, help="The comic archive to convert")
    parser.add_argument("--series", default="G.I. Joe", help="The name of comic series")
    parser.add_argument("--publisher", default="Marvel", help="The publisher of the comic")
    parser.add_argument("--inum", type=int, default=-1, help="The issue number")
    parser.add_argument("--iyear", type=int, default=-1, help="The issue year of publication")
    args = parser.parse_args()

    odir = Path("temp_out")
    prepare_out_dir(odir)
    title = f"{args.series} {args.inum:03d}"
    result_epub = Path(f"{title}.epub")
    if result_epub.exists():
        print("outfile already exists!", file=sys.stderr)
    else:
        metadata = {"title": title, "year": args.iyear, "author": args.publisher}
        create_optimized_zip(args.comic, metadata, result_epub)


if __name__ == "__main__":
    main()
