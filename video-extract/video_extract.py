#!/usr/bin/env python3
"""Pick a video from a folder, then extract its audio, screenshots, and/or a transcript.

The script lists the videos in a folder, asks you to pick one by number, and then
asks whether to extract the audio, take screenshots (and how many seconds apart),
and transcribe the speech (and whether to include timestamps). Results land in a
``<name>_<ext>_extracted`` folder next to the video, or under ``--output-dir``.

Audio and screenshots need only ffmpeg and ffprobe on PATH. Transcription also
needs the optional faster-whisper package; its Whisper model downloads on first
use and is cached after that.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


VIDEO_EXTENSIONS = frozenset(
    {
        ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
        ".mpeg", ".mpg", ".mts", ".ogv", ".ts", ".vob", ".webm", ".wmv",
    }
)
DEFAULT_INTERVAL_SECONDS = 10.0
DEFAULT_WHISPER_MODEL = "small"
LARGE_SCREENSHOT_COUNT = 1000  # confirm before writing more screenshots than this
WHISPER_SAMPLE_RATE = 16_000
TS_SYNC_BYTE = 0x47
TS_PACKET_SIZES = (188, 192)  # MPEG-TS, and M2TS packets with a 4-byte timecode prefix
TS_PACKETS_TO_CHECK = 5

Ask = Callable[[str], str]


class ExtractError(RuntimeError):
    """A step failed in an expected way; report it without a traceback."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None  # seconds; None when the container does not say
    has_video: bool  # a real video stream, not just embedded cover art
    has_audio: bool


@dataclass(frozen=True)
class Plan:
    audio: bool = False
    screenshot_interval: float | None = None  # None means no screenshots
    transcript: bool = False
    timestamps: bool = False

    @property
    def empty(self) -> bool:
        return not (self.audio or self.screenshot_interval or self.transcript)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def say(message: str = "") -> None:
    # Flush so our lines stay in order with ffmpeg's progress, which goes straight to the console.
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Finding and choosing a video
# ---------------------------------------------------------------------------


def find_videos(directory: Path) -> list[Path]:
    """Video files directly inside ``directory`` (not subfolders), in natural order."""
    videos = []
    for entry in directory.iterdir():
        suffix = entry.suffix.lower()
        if entry.name.startswith(".") or suffix not in VIDEO_EXTENSIONS or not entry.is_file():
            continue  # dotfiles include macOS "._name.mp4" metadata files on shared drives
        if suffix == ".ts" and not looks_like_mpeg_ts(entry):
            continue  # .ts is also the TypeScript extension
        videos.append(entry)
    return sorted(videos, key=lambda path: (natural_key(path.name), path.name))


def looks_like_mpeg_ts(path: Path) -> bool:
    """True when the file starts with evenly spaced MPEG transport-stream sync bytes."""
    try:
        with path.open("rb") as handle:
            head = handle.read(max(TS_PACKET_SIZES) * (TS_PACKETS_TO_CHECK + 1))
    except OSError:
        return False
    for size in TS_PACKET_SIZES:
        for offset in range(size):
            positions = range(offset, offset + size * TS_PACKETS_TO_CHECK, size)
            if positions[-1] < len(head) and all(head[pos] == TS_SYNC_BYTE for pos in positions):
                return True
    return False


def natural_key(name: str) -> tuple:
    """Sort key that orders "ep2" before "ep10"; digit runs sit at odd indexes."""
    parts = re.split(r"(\d+)", name.casefold())
    return tuple(int(part) if index % 2 else part for index, part in enumerate(parts))


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def choose_video(videos: Sequence[Path], ask: Ask) -> Path | None:
    """Print the numbered list and return the chosen video, or None to quit."""
    width = len(str(len(videos)))
    for number, video in enumerate(videos, start=1):
        try:
            size = format_size(video.stat().st_size)
        except OSError:
            size = "size unknown"
        say(f"  {number:>{width}}. {video.name}  ({size})")
    while True:
        answer = ask(f"Select a video [1-{len(videos)}, q to quit]: ").strip().lower()
        if answer in {"q", "quit", "exit"}:
            return None
        try:
            number = int(answer)
        except ValueError:
            number = 0
        if 1 <= number <= len(videos):
            return videos[number - 1]
        say(f"  Enter a number from 1 to {len(videos)}, or q to quit.")


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def ask_yes_no(question: str, ask: Ask, *, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = ask(f"{question} {hint}: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        say("  Please answer y or n.")


def estimate_screenshot_count(duration: float | None, interval: float) -> int | None:
    """Screenshots are taken at 0, interval, 2*interval, ... while still inside the video."""
    if not duration:
        return None
    return max(1, math.ceil(duration / interval))


def ask_interval(ask: Ask, duration: float | None) -> float:
    default = seconds_label(DEFAULT_INTERVAL_SECONDS)
    while True:
        answer = ask(f"Seconds between screenshots [{default}]: ").strip()
        try:
            interval = float(answer) if answer else DEFAULT_INTERVAL_SECONDS
        except ValueError:
            interval = math.nan
        if not math.isfinite(interval) or interval <= 0:
            say("  Enter a number of seconds greater than 0, such as 10 or 0.5.")
            continue
        count = estimate_screenshot_count(duration, interval)
        if count is not None and count > LARGE_SCREENSHOT_COUNT:
            question = f"  That is about {count:,} screenshots. Use this interval?"
            if not ask_yes_no(question, ask, default=False):
                continue
        return interval


def transcription_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def ask_plan(info: MediaInfo, ask: Ask) -> Plan:
    """Ask the three questions, skipping any the file cannot support."""
    audio = transcript = timestamps = False
    interval = None
    if info.has_audio:
        audio = ask_yes_no("Extract the audio as MP3?", ask, default=False)
    else:
        say("This file has no audio track, so audio and transcript extraction are skipped.")
    if not info.has_video:
        say("This file has no video track, so screenshots are skipped.")
    elif ask_yes_no("Extract screenshots?", ask, default=False):
        interval = ask_interval(ask, info.duration)
    if info.has_audio:
        if not transcription_available():
            say("Transcription needs the faster-whisper package, which is not installed. Install it with:")
            say(f'    "{sys.executable}" -m pip install faster-whisper')
        elif ask_yes_no("Extract a transcript?", ask, default=False):
            transcript = True
            timestamps = ask_yes_no("Include timestamps in the transcript?", ask, default=True)
    return Plan(audio=audio, screenshot_interval=interval, transcript=transcript, timestamps=timestamps)


# ---------------------------------------------------------------------------
# Formatting and naming
# ---------------------------------------------------------------------------


def format_timestamp(seconds: float) -> str:
    """HH:MM:SS.mmm"""
    total_ms = max(0, round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def seconds_label(value: float) -> str:
    """10.0 -> "10", 2.5 -> "2.5"."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def output_folder_name(video: Path) -> str:
    # The extension is part of the name so talk.mp4 and talk.mkv never share a folder.
    return f"{video.stem}_{video.suffix[1:].lower()}_extracted"


def screenshot_folder_name(interval: float) -> str:
    return f"screenshots_{seconds_label(interval)}s"


def screenshot_name(index: int, interval: float) -> str:
    """Name for the screenshot taken at ``index * interval`` seconds."""
    stamp = format_timestamp(index * interval).replace(":", "-")
    if float(interval).is_integer():
        stamp = stamp[: -len(".000")]  # whole-second intervals never need milliseconds
    return f"shot_{stamp}.jpg"


def transcript_line(segment: Segment, *, timestamps: bool) -> str:
    text = segment.text.strip()
    if not timestamps:
        return text
    return f"[{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}] {text}"


def partial_path(path: Path) -> Path:
    """Work-in-progress name; a file only gets its final name once it is complete."""
    return path.with_name(path.name + ".partial")


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg
# ---------------------------------------------------------------------------


def probe(video: Path) -> MediaInfo:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type:stream_disposition=attached_pic",
            "-of", "json", str(video),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise ExtractError(f"ffprobe could not read {video.name}: {detail}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    has_video = any(
        stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")
        for stream in streams
    )
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    if duration is not None and not (math.isfinite(duration) and duration > 0):
        duration = None
    return MediaInfo(duration=duration, has_video=has_video, has_audio=has_audio)


def run_ffmpeg(args: Sequence[str], *, progress: bool) -> None:
    """Run ffmpeg; with ``progress`` its live status line goes straight to the console."""
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    command.append("-stats" if progress else "-nostats")
    command.extend(args)
    result = subprocess.run(
        command,
        stderr=None if progress else subprocess.PIPE,
        text=not progress,
        encoding=None if progress else "utf-8",
        errors=None if progress else "replace",
        check=False,
    )
    if result.returncode != 0:
        detail = "see ffmpeg's messages above" if progress else (result.stderr or "").strip()
        raise ExtractError(f"ffmpeg failed with exit code {result.returncode}: {detail}")


def extract_audio(video: Path, destination: Path, *, progress: bool = True) -> Path:
    """Write the default audio track as a VBR MP3 (LAME quality 2, roughly 190 kbit/s)."""
    partial = partial_path(destination)
    try:
        run_ffmpeg(
            ["-y", "-i", str(video), "-vn", "-sn", "-dn",
             "-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3", str(partial)],
            progress=progress,
        )
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def extract_screenshots(video: Path, destination: Path, interval: float, *, progress: bool = True) -> list[Path]:
    """Save a JPEG of the frame on screen at 0, interval, 2*interval, ... seconds.

    ``fps=...:round=up`` is what pins frame N to exactly N * interval; the default
    rounding (``near``) picks frames about half an interval late. ``scale=iw*sar``
    turns anamorphic (non-square pixel) video into its true display shape.
    """
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".partial-", dir=destination))
    try:
        # ffmpeg reads % in the output path as a sequence pattern; %% is a literal %.
        pattern = str(staging).replace("%", "%%") + os.sep + "%06d.jpg"
        run_ffmpeg(
            ["-y", "-i", str(video), "-map", "0:V:0",
             "-vf", f"fps=1/{float(interval)!r}:round=up,scale=iw*sar:ih,setsar=1",
             "-q:v", "2", "-start_number", "0", pattern],
            progress=progress,
        )
        shots = []
        for frame in sorted(staging.glob("*.jpg")):
            target = destination / screenshot_name(int(frame.stem), interval)
            os.replace(frame, target)
            shots.append(target)
        return shots
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


def load_audio_16k(video: Path):
    """Decode the default audio track to 16 kHz mono float32 samples (a NumPy array).

    faster-whisper can decode files itself, but its decoder in 1.2.1 fails with
    PyAV 19 (``av.open()`` no longer accepts ``metadata_errors``), so ffmpeg does it.
    """
    import numpy  # installed with faster-whisper

    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(WHISPER_SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit code {result.returncode}"
        raise ExtractError(f"ffmpeg could not decode the audio: {detail}")
    return numpy.frombuffer(result.stdout, dtype=numpy.float32)


def whisper_segments(
    video: Path, *, model_name: str, language: str | None, word_timestamps: bool
) -> tuple[Iterator[Segment], float | None]:
    """Start a faster-whisper transcription; segments are produced lazily as it runs.

    VAD skips silence, and word timestamps snap each segment to the words actually
    spoken; without them, segment starts can land seconds before the speech.
    """
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import WhisperModel

    say("Decoding audio...")
    audio = load_audio_16k(video)
    say(f"Loading the Whisper '{model_name}' model (the first run downloads it, which can take a few minutes)...")
    # Third-party boundary: download, file, and runtime errors differ across versions, so
    # any failure to start is reported as a failed step. Errors during the transcription
    # itself are not caught; they keep their traceback.
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    except Exception as exc:
        raise ExtractError(
            f"could not load Whisper model {model_name!r} "
            f"(check the name and, on first use, your internet connection): {exc}"
        ) from exc
    say("Transcribing...")
    try:
        segments, info = model.transcribe(
            audio, language=language, vad_filter=True, word_timestamps=word_timestamps
        )
    except Exception as exc:  # e.g. an unknown --language code
        raise ExtractError(f"Whisper could not start: {exc}") from exc
    if language is None:
        say(f"Detected language: {info.language} ({info.language_probability:.0%} confidence)")
    plain = (Segment(start=item.start, end=item.end, text=item.text) for item in segments)
    return plain, info.duration


def write_transcript(
    segments: Iterable[Segment], destination: Path, *, timestamps: bool, duration: float | None
) -> int:
    """Stream segments into ``destination``, echoing progress; returns the line count."""
    partial = partial_path(destination)
    lines = 0
    try:
        with partial.open("w", encoding="utf-8") as handle:
            for segment in segments:
                if not segment.text.strip():
                    continue
                # Echo exactly what is written: without word timestamps, segment times are rough.
                line = transcript_line(segment, timestamps=timestamps)
                handle.write(line + "\n")
                lines += 1
                done = f"{min(segment.end / duration, 1.0):4.0%}" if duration else "  ? "
                say(f"  [{done}] {line}")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return lines


def transcribe(
    video: Path, destination: Path, *, timestamps: bool, model_name: str, language: str | None
) -> int:
    segments, duration = whisper_segments(
        video, model_name=model_name, language=language, word_timestamps=timestamps
    )
    return write_transcript(segments, destination, timestamps=timestamps, duration=duration)


# ---------------------------------------------------------------------------
# Running the plan
# ---------------------------------------------------------------------------


def run_plan(
    video: Path, plan: Plan, out_dir: Path, *, model_name: str, language: str | None
) -> tuple[list[str], list[str]]:
    """Run each selected step; one failing step does not stop the others.

    Returns (summary lines for finished steps, failure messages).
    """
    steps: list[tuple[str, Callable[[], str]]] = []
    if plan.audio:
        def audio_step() -> str:
            path = extract_audio(video, out_dir / f"{video.stem}.mp3")
            return path.name
        steps.append(("audio", audio_step))
    if plan.screenshot_interval is not None:
        interval = plan.screenshot_interval
        def screenshot_step() -> str:
            folder = out_dir / screenshot_folder_name(interval)
            shots = extract_screenshots(video, folder, interval)
            return f"{folder.name}{os.sep} ({len(shots)} images, every {seconds_label(interval)} s)"
        steps.append(("screenshots", screenshot_step))
    if plan.transcript:
        def transcript_step() -> str:
            path = out_dir / f"{video.stem}_transcript.txt"
            lines = transcribe(
                video, path, timestamps=plan.timestamps, model_name=model_name, language=language
            )
            if lines == 0:
                say("  No speech was detected.")
            detail = "with timestamps" if plan.timestamps else "no timestamps"
            return f"{path.name} ({lines} lines, {detail})"
        steps.append(("transcript", transcript_step))

    finished: list[str] = []
    failures: list[str] = []
    for label, step in steps:
        say(f"\n== {label} ==")
        try:
            finished.append(f"  {label:<12} {step()}")
        except ExtractError as exc:
            say(f"  failed: {exc}")
            failures.append(f"  {label:<12} FAILED: {exc}")
    return finished, failures


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List the videos in a folder, pick one by number, and extract its audio, "
            "screenshots, and/or transcript."
        ),
    )
    parser.add_argument(
        "directory", nargs="?", type=Path, default=Path("."),
        help="folder to scan for videos (default: the current folder)",
    )
    parser.add_argument(
        "--output-dir", type=Path, metavar="DIR",
        help="where to create the <name>_<ext>_extracted folder (default: next to the video)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_WHISPER_MODEL,
        help="Whisper model for transcripts: tiny, base, small, medium, large-v3, turbo, "
             "or a local model folder (default: %(default)s)",
    )
    parser.add_argument(
        "--language", metavar="CODE",
        help="spoken language for transcripts, such as en or de (default: detect it)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, ask: Ask = input) -> int:
    args = parse_args(argv)
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(
            f"error: {' and '.join(missing)} not found on PATH. Install FFmpeg "
            "(Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg; "
            "Debian/Ubuntu: sudo apt install ffmpeg).",
            file=sys.stderr,
        )
        return 2
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        print(f"error: not a folder: {directory}", file=sys.stderr)
        return 2
    videos = find_videos(directory)
    if not videos:
        say(f"No videos found in {directory}")
        return 1

    try:
        say(f"Videos in {directory}:")
        video = choose_video(videos, ask)
        if video is None:
            return 0
        info = probe(video)
        length = format_timestamp(info.duration)[: -len(".000")] if info.duration else "unknown length"
        say(f"\n{video.name} ({length})")
        plan = ask_plan(info, ask)
    except KeyboardInterrupt:
        say("\nCancelled.")
        return 130
    except EOFError:
        say("\nNo more input; cancelled.")
        return 1
    except ExtractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if plan.empty:
        say("Nothing selected, so there is nothing to do.")
        return 0

    out_dir = (args.output_dir.expanduser().resolve() if args.output_dir else video.parent) / output_folder_name(video)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create {out_dir}: {exc} (try --output-dir)", file=sys.stderr)
        return 1
    say(f"Saving to {out_dir}")
    try:
        finished, failures = run_plan(video, plan, out_dir, model_name=args.model, language=args.language)
    except KeyboardInterrupt:
        say("\nCancelled; unfinished files were removed.")
        return 130

    say(f"\nDone. Output folder: {out_dir}")
    for line in finished + failures:
        say(line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
