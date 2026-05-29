#!/usr/bin/env python3
"""zip-to-aac: Convert audio in ZIPs to AAC with cover embedding via ffmpeg.

Python stdlib reimplementation of the Go version (see go-version/main.go).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Exceptions (more Pythonic error handling than Optional[str] returns)
# --------------------------------------------------------------------------- #

class ZipToAacError(Exception):
    """Base class for all zip-to-aac errors."""

class FFmpegNotFoundError(ZipToAacError):
    """Required ffmpeg or ffprobe binary was not found in PATH."""

class FFmpegError(ZipToAacError):
    """An ffmpeg operation (convert or cover embed) failed for a track."""

    def __init__(self, message: str, *, track: str | Path, stderr: str = "") -> None:
        super().__init__(message)
        self.track = Path(track)
        self.stderr = stderr

    def __str__(self) -> str:
        base = super().__str__()
        if self.stderr:
            return f"{base}\n--- ffmpeg stderr ---\n{self.stderr.strip()}"
        return base


class ZipProcessingError(ZipToAacError):
    """One or more fatal errors occurred while processing a single ZIP file."""

    def __init__(
        self,
        zip_path: str | Path,
        message: str,
        *,
        underlying: list[Exception] | None = None,
    ) -> None:
        self.zip_path = Path(zip_path)
        self.underlying = underlying or []
        super().__init__(f"{self.zip_path.name}: {message}")


def get_codec(input_path: str) -> str:
    """Return the audio codec name of the first audio stream, or empty on error."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace").strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""


def get_has_cover(input_path: str) -> bool:
    """Return True if the file has an attached picture (video stream with attached_pic disposition)."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        input_path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        data = json.loads(out)
        for stream in data.get("streams", []):
            if (
                stream.get("codec_type") == "video"
                and stream.get("disposition", {}).get("attached_pic") == 1
            ):
                return True
        return False
    except Exception:
        return False


def _copy_file(src: Path, dst: Path) -> None:
    """Copy file contents (binary, preserving basic metadata where possible)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, dst)
    try:
        os.utime(dst, (src.stat().st_atime, src.stat().st_mtime))
    except OSError:
        pass


def _run_ffmpeg(args: list[str], *, track: str | Path) -> None:
    """Run an ffmpeg command. Raises FFmpegError (or FFmpegNotFoundError) on failure."""
    cmd = ["ffmpeg", *args]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        raise FFmpegNotFoundError(
            "ffmpeg not found in PATH (required for audio conversion and cover embedding)"
        ) from None
    except subprocess.CalledProcessError as e:
        errout = (e.stderr or e.stdout or b"").decode("utf-8", errors="replace")
        raise FFmpegError(
            f"ffmpeg failed for track {Path(track).name}",
            track=track,
            stderr=errout,
        ) from e
    except OSError as e:
        raise FFmpegError(f"Failed to execute ffmpeg for {Path(track).name}: {e}", track=track) from e


def _embed_cover(input_path: Path, cover_path: Path, output_path: Path, *, is_mp3: bool) -> None:
    """Embed cover art into an existing MP3/M4A via stream copy."""
    args = [
        "-i", str(input_path),
        "-i", str(cover_path),
        "-map", "0",
        "-map", "-0:v",
        "-map", "1:v",
        "-c", "copy",
        "-map_metadata", "0",
        "-disposition:v", "attached_pic",
    ]
    if is_mp3:
        args.extend(["-id3v2_version", "3"])
    else:
        args.extend(["-movflags", "+faststart"])
    args.append(str(output_path))
    _run_ffmpeg(args, track=input_path)


def _convert_to_aac(input_path: Path, cover_path: Path | None, output_path: Path) -> None:
    """Convert arbitrary audio to AAC .m4a (with optional cover)."""
    args: list[str] = ["-i", str(input_path)]
    if cover_path:
        args += ["-i", str(cover_path), "-map", "0:a:0", "-map", "1:v:0"]
    else:
        args += ["-map", "0:a:0"]

    args += [
        "-map_metadata", "0",
        "-id3v2_version", "3",
        "-af", "aresample=resampler=soxr:precision=33:osr=44100",
        "-c:a", "aac_at",
        "-aac_at_mode", "cvbr",
        "-b:a", "256k",
        "-movflags", "+faststart",
    ]
    if cover_path:
        args += ["-c:v", "copy", "-disposition:v:0", "attached_pic"]
    args.append(str(output_path))
    _run_ffmpeg(args, track=input_path)


# --------------------------------------------------------------------------- #
# Cover preparation (ImageMagick) and track processing
# --------------------------------------------------------------------------- #

COVER_MAX_DIMENSION = 600
COVER_MAX_SIZE_BYTES = 150 * 1024


def _prepare_cover(original: Path, work_dir: Path) -> Path:
    """Return best cover (original or smaller 600x600 q75 JPEG if beneficial).

    Uses `magick identify -ping` for dimensions and `magick ... -resize ...> -quality 75`
    for the shrink. Only switches to the resized file when it is strictly smaller.
    Any failure (missing magick, etc.) falls back to the original (non-fatal).
    """
    try:
        size = original.stat().st_size
        ident = subprocess.run(
            ["magick", "identify", "-ping", "-format", "%w %h", str(original)],
            capture_output=True,
            text=True,
            check=True,
        )
        w, h = map(int, ident.stdout.strip().split())
        if w <= COVER_MAX_DIMENSION and h <= COVER_MAX_DIMENSION and size <= COVER_MAX_SIZE_BYTES:
            return original

        resized = work_dir / "resized_cover.jpg"
        subprocess.run(
            [
                "magick",
                str(original),
                "-resize",
                f"{COVER_MAX_DIMENSION}x{COVER_MAX_DIMENSION}>",
                "-quality",
                "75",
                str(resized),
            ],
            check=True,
            capture_output=True,
        )
        if resized.stat().st_size < size:
            print(f"  Resized cover {original.name} ({size} → {resized.stat().st_size} bytes)", file=sys.stderr)
            return resized
        return original
    except FileNotFoundError:
        return original  # magick not present
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"Warning: magick cover resize failed for {original.name} ({e}); using original.", file=sys.stderr)
        return original


def _process_track(input_path: Path, cover_path: Path | None, output_dir: Path) -> None:
    """Copy (good MP3/AAC) or convert the track. Raises on fatal errors."""
    ext = input_path.suffix.lower()
    codec = get_codec(str(input_path))
    has_embedded = get_has_cover(str(input_path))
    is_good_mp3 = (ext == ".mp3") and (codec == "mp3")
    is_good_aac = (ext == ".m4a") and (codec == "aac")

    output_dir.mkdir(parents=True, exist_ok=True)

    if is_good_mp3 or is_good_aac:
        out = output_dir / input_path.name
        if has_embedded or not cover_path:
            _copy_file(input_path, out)
        else:
            _embed_cover(input_path, cover_path, out, is_mp3=is_good_mp3)
    else:
        out = output_dir / f"{input_path.stem}.m4a"
        _convert_to_aac(input_path, cover_path, out)


# --------------------------------------------------------------------------- #
# Main ZIP processing
# --------------------------------------------------------------------------- #

def process_zip(zip_file: str | Path) -> None:
    """Process one ZIP: extract, select+prepare cover (with folder/cover preference),
    run up to 8 parallel ffmpeg jobs, write results to ./<basename>/.

    Raises ZipProcessingError (or subclass) on failure.
    """
    zip_path = Path(zip_file)
    base = zip_path.with_suffix("").name
    output_dir = Path(base)

    output_dir.mkdir(parents=True, exist_ok=True)

    music_files: list[Path] = []
    best_cover: Path | None = None

    try:
        with tempfile.TemporaryDirectory(prefix="zip_extract_") as tmp:
            tmp_path = Path(tmp)

            with zipfile.ZipFile(zip_file, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    target = tmp_path / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

                    ext = Path(info.filename).suffix.lower()
                    if ext in {".jpg", ".jpeg"}:
                        fname = info.filename
                        # Prefer "cover"/"Cover" or "folder"/"Folder" (case-sensitive prefix, like original)
                        is_preferred = fname.startswith(("cover", "Cover", "folder", "Folder"))
                        if best_cover is None or is_preferred:
                            best_cover = target
                    elif ext in {".flac", ".m4a", ".mp3", ".ogg"}:
                        music_files.append(target)

            if not music_files:
                print(f"No music files found in {zip_file}", file=sys.stderr)
                return

            final_cover: Path | None = None
            if best_cover is not None:
                final_cover = _prepare_cover(best_cover, tmp_path)
            else:
                print(
                    f"Warning: No cover art found in ZIP {zip_file}. "
                    "Proceeding without embedding cover where necessary.",
                    file=sys.stderr,
                )

            print(f"Converting Album: {base} ({len(music_files)} tracks, max 8 parallel)")

            errors: list[tuple[Path, Exception]] = []
            progress_lock = threading.Lock()
            done = 0
            total = len(music_files)

            def worker(p: Path) -> tuple[Path, Exception | None]:
                try:
                    _process_track(p, final_cover, output_dir)
                    return (p, None)
                except Exception as exc:  # noqa: BLE001
                    return (p, exc)

            with ThreadPoolExecutor(max_workers=8) as ex:
                future_map = {ex.submit(worker, p): p for p in music_files}
                for fut in as_completed(future_map):
                    p, err = fut.result()
                    with progress_lock:
                        done += 1
                        status = "ERROR" if err else "done"
                        print(f"  [{done}/{total}] {p.name} -> {status}")
                    if err is not None:
                        errors.append((p, err))

            if errors:
                for p, e in errors:
                    print(f"Error on {p.name}: {e}", file=sys.stderr)
                raise ZipProcessingError(
                    zip_file,
                    f"encountered {len(errors)} track processing errors",
                    underlying=[e for _, e in errors],
                )

    except zipfile.BadZipFile as e:
        raise ZipProcessingError(zip_file, "not a valid zip file") from e
    except ZipToAacError:
        raise
    except Exception as e:
        raise ZipProcessingError(zip_file, f"unexpected error: {e}") from e


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zip-to-aac",
        description=(
            "Convert audio files inside ZIP(s) to AAC (or copy MP3/AAC), "
            "embedding cover art (with optional ImageMagick downscaling). "
            "Output directory is named after the ZIP."
        ),
    )
    parser.add_argument(
        "zips",
        nargs="+",
        metavar="ZIPFILE",
        help="One or more zip files containing audio tracks and optional cover/folder.jpg",
    )
    args = parser.parse_args()

    had_error = False
    for z in args.zips:
        try:
            process_zip(z)
        except ZipToAacError as e:
            print(f"Failed processing {z}: {e}", file=sys.stderr)
            had_error = True

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
