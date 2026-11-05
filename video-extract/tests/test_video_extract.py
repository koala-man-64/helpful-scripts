"""Tests for video_extract.

Pure logic (listing, prompts, naming, transcript formatting) runs everywhere.
Media tests generate tiny clips with ffmpeg's built-in test sources and are
skipped when ffmpeg/ffprobe are not on PATH. The real-Whisper test is opt-in
(it downloads a model): set VIDEO_EXTRACT_LIVE_WHISPER=1 with faster-whisper
installed, and optionally VIDEO_EXTRACT_LIVE_MODEL (default: tiny).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import video_extract as vx


HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")


def scripted(*answers: str):
    """A stand-in for input() that replays answers and records each prompt."""
    pending = list(answers)
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        if not pending:
            raise EOFError
        return pending.pop(0)

    ask.prompts = prompts
    ask.pending = pending
    return ask


def quietly(func, *args, **kwargs):
    """Call func with stdout captured; return (result, captured text)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func(*args, **kwargs)
    return result, buffer.getvalue()


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


def ffprobe_value(path: Path, entries: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def gray_level(image: Path) -> int:
    """Luma of the first pixel, decoded by ffmpeg (no imaging library needed)."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(image),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    return raw[0]


def write_ts_like(path: Path, packet_size: int, offset: int = 0, packets: int = 6) -> None:
    data = bytearray(packet_size * packets + offset)
    for index in range(packets):
        data[offset + index * packet_size] = vx.TS_SYNC_BYTE
    path.write_bytes(bytes(data))


class NamingTests(unittest.TestCase):
    def test_natural_key_orders_numbers_numerically_and_ignores_case(self):
        names = ["ep10.mp4", "ep2.mp4", "Ep1.mp4", "ep02b.mp4"]
        self.assertEqual(
            sorted(names, key=vx.natural_key),
            ["Ep1.mp4", "ep2.mp4", "ep02b.mp4", "ep10.mp4"],
        )

    def test_natural_key_handles_non_ascii_digit_characters(self):
        # Superscript two is a digit to str.isdigit() but not to int().
        self.assertIsInstance(vx.natural_key("clip\N{SUPERSCRIPT TWO} 3.mp4"), tuple)

    def test_format_size(self):
        self.assertEqual(vx.format_size(0), "0 B")
        self.assertEqual(vx.format_size(1023), "1023 B")
        self.assertEqual(vx.format_size(1536), "1.5 KB")
        self.assertEqual(vx.format_size(5 * 1024**3), "5.0 GB")
        self.assertEqual(vx.format_size(3 * 1024**4), "3.0 TB")

    def test_format_timestamp(self):
        self.assertEqual(vx.format_timestamp(0), "00:00:00.000")
        self.assertEqual(vx.format_timestamp(3661.5), "01:01:01.500")
        self.assertEqual(vx.format_timestamp(59.9996), "00:01:00.000")
        self.assertEqual(vx.format_timestamp(-2), "00:00:00.000")

    def test_screenshot_names_encode_their_time(self):
        self.assertEqual(vx.screenshot_name(0, 10), "shot_00-00-00.jpg")
        self.assertEqual(vx.screenshot_name(9, 10.0), "shot_00-01-30.jpg")
        self.assertEqual(vx.screenshot_name(3, 2.5), "shot_00-00-07.500.jpg")
        self.assertEqual(vx.screenshot_name(3, 0.1), "shot_00-00-00.300.jpg")
        self.assertEqual(vx.screenshot_name(400, 10), "shot_01-06-40.jpg")

    def test_folder_names(self):
        self.assertEqual(vx.screenshot_folder_name(10.0), "screenshots_10s")
        self.assertEqual(vx.screenshot_folder_name(2.5), "screenshots_2.5s")
        self.assertEqual(vx.output_folder_name(Path("My Talk.MP4")), "My Talk_mp4_extracted")

    def test_estimate_screenshot_count(self):
        self.assertEqual(vx.estimate_screenshot_count(30, 7), 5)  # 0, 7, 14, 21, 28
        self.assertEqual(vx.estimate_screenshot_count(30, 10), 3)  # 0, 10, 20
        self.assertEqual(vx.estimate_screenshot_count(2, 10), 1)
        self.assertIsNone(vx.estimate_screenshot_count(None, 5))

    def test_transcript_line(self):
        segment = vx.Segment(start=61.25, end=64.0, text="  Hello there. ")
        self.assertEqual(vx.transcript_line(segment, timestamps=False), "Hello there.")
        self.assertEqual(
            vx.transcript_line(segment, timestamps=True),
            "[00:01:01.250 --> 00:01:04.000] Hello there.",
        )


class FindVideosTests(unittest.TestCase):
    def test_lists_only_videos_in_natural_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ("b.mp4", "a.MKV", "ep10.webm", "ep2.webm", "notes.txt",
                         ".hidden.mp4", "._b.mp4"):
                (folder / name).write_bytes(b"x")
            (folder / "app.ts").write_text("export const greeting: string = 'hi';\n" * 50)
            write_ts_like(folder / "recording.ts", 188)
            (folder / "folder.mp4").mkdir()
            (folder / "nested").mkdir()
            (folder / "nested" / "deep.mp4").write_bytes(b"x")

            names = [path.name for path in vx.find_videos(folder)]

        self.assertEqual(names, ["a.MKV", "b.mp4", "ep2.webm", "ep10.webm", "recording.ts"])

    def test_mpeg_ts_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_ts_like(folder / "plain.ts", 188)
            write_ts_like(folder / "m2ts_style.ts", 192, offset=4)
            write_ts_like(folder / "unaligned.ts", 188, offset=17)
            write_ts_like(folder / "too_short.ts", 188, packets=2)
            (folder / "source.ts").write_text("G" * 10 + "import x from 'y';\n" * 80)
            self.assertTrue(vx.looks_like_mpeg_ts(folder / "plain.ts"))
            self.assertTrue(vx.looks_like_mpeg_ts(folder / "m2ts_style.ts"))
            self.assertTrue(vx.looks_like_mpeg_ts(folder / "unaligned.ts"))
            self.assertFalse(vx.looks_like_mpeg_ts(folder / "too_short.ts"))
            self.assertFalse(vx.looks_like_mpeg_ts(folder / "source.ts"))


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.videos = []
        for name in ("one.mp4", "two.mp4", "three.mp4"):
            path = Path(self.tmp.name) / name
            path.write_bytes(b"x" * 2048)
            self.videos.append(path)

    def test_choose_video_reprompts_until_a_valid_index(self):
        ask = scripted("", "abc", "0", "4", "\N{SUPERSCRIPT TWO}", "2")
        chosen, output = quietly(vx.choose_video, self.videos, ask)
        self.assertEqual(chosen, self.videos[1])
        self.assertEqual(len(ask.prompts), 6)
        self.assertIn("1. one.mp4  (2.0 KB)", output)
        self.assertEqual(output.count("Enter a number from 1 to 3"), 5)

    def test_choose_video_quit(self):
        chosen, _ = quietly(vx.choose_video, self.videos, scripted("Q"))
        self.assertIsNone(chosen)

    def test_ask_yes_no(self):
        self.assertTrue(quietly(vx.ask_yes_no, "Go?", scripted(""), default=True)[0])
        self.assertFalse(quietly(vx.ask_yes_no, "Go?", scripted(""), default=False)[0])
        self.assertTrue(quietly(vx.ask_yes_no, "Go?", scripted("YES"), default=False)[0])
        ask = scripted("nope", "n")
        answer, output = quietly(vx.ask_yes_no, "Go?", ask, default=True)
        self.assertFalse(answer)
        self.assertIn("Please answer y or n", output)
        self.assertEqual(ask.prompts, ["Go? [Y/n]: "] * 2)

    def test_ask_interval_default_and_validation(self):
        self.assertEqual(quietly(vx.ask_interval, scripted(""), 60)[0], 10.0)
        ask = scripted("-1", "0", "inf", "nan", "abc", "2.5")
        interval, output = quietly(vx.ask_interval, ask, 60)
        self.assertEqual(interval, 2.5)
        self.assertEqual(output.count("greater than 0"), 5)

    def test_ask_interval_confirms_very_large_screenshot_counts(self):
        # One hour at 0.1 s would be 36,000 images: declined, then 10 s is accepted.
        ask = scripted("0.1", "n", "10")
        interval, output = quietly(vx.ask_interval, ask, 3600)
        self.assertEqual(interval, 10.0)
        self.assertIn("about 36,000 screenshots", output + "".join(ask.prompts))
        # Accepting the large count keeps it.
        interval, _ = quietly(vx.ask_interval, scripted("1", "y"), 3600)
        self.assertEqual(interval, 1.0)
        # Unknown duration: nothing to confirm.
        interval, _ = quietly(vx.ask_interval, scripted("0.1"), None)
        self.assertEqual(interval, 0.1)


class AskPlanTests(unittest.TestCase):
    full = vx.MediaInfo(duration=120.0, has_video=True, has_audio=True)

    def test_all_three_with_timestamps(self):
        ask = scripted("y", "y", "5", "y", "")
        with mock.patch.object(vx, "transcription_available", return_value=True):
            plan, _ = quietly(vx.ask_plan, self.full, ask)
        self.assertEqual(
            plan, vx.Plan(audio=True, screenshot_interval=5.0, transcript=True, timestamps=True)
        )
        self.assertEqual(ask.pending, [])
        self.assertEqual(
            ask.prompts,
            [
                "Extract the audio as MP3? [y/N]: ",
                "Extract screenshots? [y/N]: ",
                "Seconds between screenshots [10]: ",
                "Extract a transcript? [y/N]: ",
                "Include timestamps in the transcript? [Y/n]: ",
            ],
        )

    def test_transcript_without_timestamps(self):
        ask = scripted("n", "n", "y", "n")
        with mock.patch.object(vx, "transcription_available", return_value=True):
            plan, _ = quietly(vx.ask_plan, self.full, ask)
        self.assertEqual(plan, vx.Plan(transcript=True, timestamps=False))

    def test_missing_faster_whisper_skips_the_question_with_install_hint(self):
        ask = scripted("n", "n")
        with mock.patch.object(vx, "transcription_available", return_value=False):
            plan, output = quietly(vx.ask_plan, self.full, ask)
        self.assertTrue(plan.empty)
        self.assertEqual(len(ask.prompts), 2)
        self.assertIn("-m pip install faster-whisper", output)

    def test_no_audio_track_only_offers_screenshots(self):
        ask = scripted("y", "")
        info = vx.MediaInfo(duration=30.0, has_video=True, has_audio=False)
        with mock.patch.object(vx, "transcription_available", return_value=True):
            plan, output = quietly(vx.ask_plan, info, ask)
        self.assertEqual(plan, vx.Plan(screenshot_interval=10.0))
        self.assertIn("no audio track", output)
        self.assertEqual(len(ask.prompts), 2)

    def test_no_video_track_skips_screenshots(self):
        ask = scripted("y", "n")
        info = vx.MediaInfo(duration=30.0, has_video=False, has_audio=True)
        with mock.patch.object(vx, "transcription_available", return_value=True):
            plan, output = quietly(vx.ask_plan, info, ask)
        self.assertEqual(plan, vx.Plan(audio=True))
        self.assertIn("no video track", output)


class TranscriptWriterTests(unittest.TestCase):
    segments = [
        vx.Segment(0.0, 2.66, " The quick brown fox jumps over the lazy dog."),
        vx.Segment(3.0, 4.0, "   "),
        vx.Segment(7.62, 11.28, " Screenshots and audio are extracted with FFmpeg."),
    ]

    def test_with_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "talk_transcript.txt"
            lines, output = quietly(
                vx.write_transcript, iter(self.segments), target, timestamps=True, duration=19.5
            )
            self.assertEqual(lines, 2)
            self.assertEqual(
                target.read_text(encoding="utf-8").splitlines(),
                [
                    "[00:00:00.000 --> 00:00:02.660] The quick brown fox jumps over the lazy dog.",
                    "[00:00:07.620 --> 00:00:11.280] Screenshots and audio are extracted with FFmpeg.",
                ],
            )
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), [target.name])
        self.assertIn("[ 58%] [00:00:07.620 --> 00:00:11.280]", output)

    def test_without_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "talk_transcript.txt"
            _, output = quietly(
                vx.write_transcript, iter(self.segments), target, timestamps=False, duration=None
            )
            self.assertEqual(
                target.read_text(encoding="utf-8").splitlines(),
                [
                    "The quick brown fox jumps over the lazy dog.",
                    "Screenshots and audio are extracted with FFmpeg.",
                ],
            )
        # Progress echoes the file's lines; rough segment times are not shown.
        self.assertIn("[  ? ] The quick brown fox", output)
        self.assertNotIn("-->", output)

    def test_failure_mid_stream_leaves_no_file_behind(self):
        def broken():
            yield self.segments[0]
            raise RuntimeError("decoder exploded")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "talk_transcript.txt"
            with self.assertRaisesRegex(RuntimeError, "decoder exploded"):
                quietly(vx.write_transcript, broken(), target, timestamps=True, duration=10)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class WhisperBoundaryTests(unittest.TestCase):
    """whisper_segments against a stand-in faster_whisper module (no download, no numpy)."""

    def run_with(self, model_class, **kwargs):
        fake_module = mock.Mock(WhisperModel=model_class)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                mock.patch.object(vx, "load_audio_16k", return_value=[0.0] * 16000):
            return quietly(
                vx.whisper_segments, Path("talk.mp4"), model_name="tiny", word_timestamps=True, **kwargs
            )

    def test_segments_and_settings(self):
        calls = {}

        class Model:
            def __init__(self, name, **options):
                calls["init"] = (name, options)

            def transcribe(self, audio, **options):
                calls["transcribe"] = options
                info = mock.Mock(language="en", language_probability=0.97, duration=9.5)
                return iter([mock.Mock(start=1.0, end=2.0, text=" Hi")]), info

        (segments, duration), output = self.run_with(Model, language=None)
        self.assertEqual(list(segments), [vx.Segment(1.0, 2.0, " Hi")])
        self.assertEqual(duration, 9.5)
        self.assertEqual(calls["init"], ("tiny", {"device": "cpu", "compute_type": "int8"}))
        self.assertEqual(
            calls["transcribe"], {"language": None, "vad_filter": True, "word_timestamps": True}
        )
        self.assertIn("Detected language: en (97% confidence)", output)

    def test_model_load_failure_is_a_step_error(self):
        class Model:
            def __init__(self, name, **options):
                raise ValueError("Invalid model size 'smal'")

        with self.assertRaisesRegex(vx.ExtractError, "could not load Whisper model 'tiny'.*smal"):
            self.run_with(Model, language=None)

    def test_bad_language_is_a_step_error(self):
        class Model:
            def __init__(self, name, **options):
                pass

            def transcribe(self, audio, **options):
                raise ValueError(f"'{options['language']}' is not a valid language code")

        with self.assertRaisesRegex(vx.ExtractError, "'xx' is not a valid language code"):
            self.run_with(Model, language="xx")


class RunPlanTests(unittest.TestCase):
    def test_a_failing_step_does_not_stop_the_others(self):
        def fake_audio(video, destination, **_):
            raise vx.ExtractError("ffmpeg failed with exit code 1: no audio encoder")

        def fake_shots(video, destination, interval, **_):
            destination.mkdir(parents=True)
            shot = destination / vx.screenshot_name(0, interval)
            shot.write_bytes(b"jpg")
            return [shot]

        def fake_segments(video, **_):
            return iter([vx.Segment(1.0, 2.0, " hi")]), 2.0

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            plan = vx.Plan(audio=True, screenshot_interval=2.5, transcript=True, timestamps=False)
            with mock.patch.object(vx, "extract_audio", fake_audio), \
                    mock.patch.object(vx, "extract_screenshots", fake_shots), \
                    mock.patch.object(vx, "whisper_segments", fake_segments):
                (finished, failures), _ = quietly(
                    vx.run_plan, Path("talk.mp4"), plan, out_dir, model_name="tiny", language=None
                )
            self.assertEqual((out_dir / "talk_transcript.txt").read_text(encoding="utf-8"), "hi\n")
        self.assertEqual(len(failures), 1)
        self.assertIn("audio", failures[0])
        self.assertIn("no audio encoder", failures[0])
        self.assertEqual(
            [line.split()[0] for line in finished], ["screenshots", "transcript"]
        )
        self.assertIn("(1 images, every 2.5 s)", finished[0])
        self.assertIn("(1 lines, no timestamps)", finished[1])


class MainArgumentTests(unittest.TestCase):
    def test_missing_ffmpeg(self):
        with mock.patch.object(vx.shutil, "which", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(vx.main(["."], ask=scripted()), 2)
        self.assertIn("ffmpeg and ffprobe not found", err.getvalue())

    @needs_ffmpeg
    def test_not_a_folder_and_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(vx.main([str(Path(tmp) / "missing")], ask=scripted()), 2)
            code, output = quietly(vx.main, [tmp], ask=scripted())
            self.assertEqual(code, 1)
            self.assertIn("No videos found", output)


@needs_ffmpeg
class MediaTests(unittest.TestCase):
    """Real ffmpeg runs against small generated clips."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls._tmp.name)
        # 30 s, 10 fps, gray level 8 * floor(t): every frame shows which second it is from.
        cls.timecode = cls.root / "timecode.mkv"
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=32x32:r=10:d=30,format=gray,geq=lum='8*floor(T)'",
               "-c:v", "ffv1", str(cls.timecode))
        # 3 s anamorphic 720x480 (pixels 32:27, so it displays at 853x480) with 5.1 audio.
        cls.anamorphic = cls.root / "anamorphic 5.1.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x480:rate=10:duration=3,setsar=32/27",
               "-f", "lavfi", "-i", "sine=f=440:d=3",
               "-filter_complex", "[1:a]pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0[a]",
               "-map", "0:v", "-map", "[a]", "-c:v", "mpeg4", "-c:a", "aac", str(cls.anamorphic))
        cls.silent = cls.root / "silent.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
               "-c:v", "mpeg4", str(cls.silent))
        # Audio plus embedded cover art: a video stream ffprobe flags as attached_pic.
        cover = cls.root / "cover.png"
        ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1", str(cover))
        cls.audio_with_cover = cls.root / "song.mp4"
        ffmpeg("-f", "lavfi", "-i", "sine=d=2", "-i", str(cover), "-map", "0", "-map", "1",
               "-c:a", "aac", "-c:v", "png", "-disposition:v:0", "attached_pic",
               str(cls.audio_with_cover))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_probe(self):
        self.assertEqual(
            vx.probe(self.anamorphic), vx.MediaInfo(duration=3.0, has_video=True, has_audio=True)
        )
        silent = vx.probe(self.silent)
        self.assertEqual((silent.has_video, silent.has_audio), (True, False))
        song = vx.probe(self.audio_with_cover)
        self.assertEqual((song.has_video, song.has_audio), (False, True))

    def test_probe_rejects_a_non_media_file(self):
        fake = self.root / "not-a-video.mp4"
        fake.write_text("hello")
        with self.assertRaisesRegex(vx.ExtractError, "ffprobe could not read not-a-video.mp4"):
            vx.probe(fake)

    def test_screenshots_land_exactly_on_each_interval(self):
        folder = self.root / "every-7s"
        shots = vx.extract_screenshots(self.timecode, folder, 7, progress=False)
        self.assertEqual(
            [shot.name for shot in shots],
            [f"shot_00-00-{second:02d}.jpg" for second in (0, 7, 14, 21, 28)],
        )
        self.assertEqual([round(gray_level(shot) / 8) for shot in shots], [0, 7, 14, 21, 28])
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [s.name for s in shots])

    def test_fractional_interval(self):
        shots = vx.extract_screenshots(self.timecode, self.root / "every-2.5s", 2.5, progress=False)
        self.assertEqual(len(shots), 12)
        self.assertEqual(shots[1].name, "shot_00-00-02.500.jpg")
        self.assertEqual(shots[-1].name, "shot_00-00-27.500.jpg")
        self.assertEqual(
            [round(gray_level(shot) / 8) for shot in shots],
            [int(index * 2.5) for index in range(12)],
        )

    def test_rerun_with_same_interval_replaces_files(self):
        folder = self.root / "rerun"
        first = vx.extract_screenshots(self.timecode, folder, 10, progress=False)
        second = vx.extract_screenshots(self.timecode, folder, 10, progress=False)
        self.assertEqual([p.name for p in first], [p.name for p in second])
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [p.name for p in second])

    def test_anamorphic_video_and_percent_in_path(self):
        folder = self.root / "100% sure" / "shots"
        shots = vx.extract_screenshots(self.anamorphic, folder, 1, progress=False)
        self.assertEqual(len(shots), 3)
        self.assertEqual(ffprobe_value(shots[0], "stream=width,height"), "853,480")

    def test_audio_is_mp3_and_downmixed_to_stereo(self):
        target = self.root / "audio" / "anamorphic 5.1.mp3"
        target.parent.mkdir()
        self.assertEqual(vx.extract_audio(self.anamorphic, target, progress=False), target)
        codec, channels = ffprobe_value(target, "stream=codec_name,channels").split(",")
        self.assertEqual((codec, channels), ("mp3", "2"))
        duration = float(ffprobe_value(target, "format=duration"))
        self.assertAlmostEqual(duration, 3.0, delta=0.15)
        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_failed_ffmpeg_run_leaves_no_partial_file(self):
        target = self.root / "no-audio" / "silent.mp3"
        target.parent.mkdir()
        with self.assertRaisesRegex(vx.ExtractError, "ffmpeg failed"):
            vx.extract_audio(self.silent, target, progress=False)
        self.assertEqual(list(target.parent.iterdir()), [])

    def test_full_interactive_run(self):
        library = self.root / "library"
        library.mkdir()
        shutil.copy(self.anamorphic, library / "talk.mp4")
        shutil.copy(self.silent, library / "b-roll.mp4")
        (library / "notes.txt").write_text("not a video")

        def fake_segments(video, *, model_name, language, word_timestamps):
            self.assertEqual((video.name, model_name, language, word_timestamps),
                             ("talk.mp4", "tiny", "en", True))
            return iter([vx.Segment(0.5, 1.75, " Hello."), vx.Segment(2.0, 2.9, " Bye.")]), 3.0

        # "9" is out of range; then pick talk.mp4 (2nd in natural order), say yes to everything.
        ask = scripted("9", "2", "y", "y", "1", "y", "y")
        with mock.patch.object(vx, "transcription_available", return_value=True), \
                mock.patch.object(vx, "whisper_segments", fake_segments):
            code, output = quietly(
                vx.main, [str(library), "--model", "tiny", "--language", "en"], ask=ask
            )

        self.assertEqual(code, 0, output)
        self.assertEqual(ask.pending, [])
        self.assertIn("1. b-roll.mp4", output)
        self.assertIn("talk.mp4 (00:00:03)", output)
        out_dir = library / "talk_mp4_extracted"
        self.assertEqual(
            sorted(p.name for p in out_dir.iterdir()),
            ["screenshots_1s", "talk.mp3", "talk_transcript.txt"],
        )
        self.assertEqual(
            sorted(p.name for p in (out_dir / "screenshots_1s").iterdir()),
            ["shot_00-00-00.jpg", "shot_00-00-01.jpg", "shot_00-00-02.jpg"],
        )
        self.assertEqual(
            (out_dir / "talk_transcript.txt").read_text(encoding="utf-8").splitlines(),
            ["[00:00:00.500 --> 00:00:01.750] Hello.", "[00:00:02.000 --> 00:00:02.900] Bye."],
        )
        self.assertIn("Done. Output folder:", output)


LIVE = os.environ.get("VIDEO_EXTRACT_LIVE_WHISPER") == "1"


@needs_ffmpeg
@unittest.skipUnless(LIVE, "set VIDEO_EXTRACT_LIVE_WHISPER=1 to run real Whisper (downloads a model)")
@unittest.skipUnless(importlib.util.find_spec("faster_whisper"), "faster-whisper not installed")
class LiveWhisperTests(unittest.TestCase):
    def test_real_transcription_pipeline_runs(self):
        model = os.environ.get("VIDEO_EXTRACT_LIVE_MODEL", "tiny")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            clip = Path(tmp) / "tone.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=4",
                   "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "mpeg4", "-c:a", "aac", str(clip))
            target = Path(tmp) / "tone_transcript.txt"
            lines, _ = quietly(
                vx.transcribe, clip, target, timestamps=True, model_name=model, language="en"
            )
            self.assertTrue(target.is_file())
            self.assertEqual(len(target.read_text(encoding="utf-8").splitlines()), lines)


if __name__ == "__main__":
    unittest.main()
