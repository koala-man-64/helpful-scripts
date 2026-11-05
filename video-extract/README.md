# Video extract

Pick a video from a folder, then pull out any of:

- **audio**: an MP3 of the soundtrack
- **screenshots**: a JPEG every *N* seconds, each named for its time in the video
- **a transcript**: speech to text, with or without a timestamp on every line

It is one Python file, `video_extract.py`. Audio and screenshots need only
FFmpeg. Transcripts use the optional
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) package, which runs
OpenAI's Whisper models locally on the CPU; the audio never leaves the machine.

## Requirements

- Python 3.10+
- FFmpeg, with `ffmpeg` and `ffprobe` on PATH: `winget install Gyan.FFmpeg`
  (Windows), `brew install ffmpeg` (macOS), `sudo apt install ffmpeg`
  (Debian/Ubuntu)
- For transcripts only: `python -m pip install faster-whisper`. The first
  transcript downloads the Whisper model from Hugging Face and caches it;
  `small` (the default) is about 490 MB, `base` about 150 MB, `tiny` about 80 MB.

## Usage

```powershell
py -3 .\video_extract.py D:\Videos
```

Without a folder argument it scans the current folder. It lists the videos,
asks you to pick one by number, then asks what to extract. A real session on a
20-second test clip (run with `--model tiny`):

```text
Videos in D:\Videos:
  1. b-roll.mp4  (302.9 KB)
  2. speech demo.mp4  (7.0 MB)
Select a video [1-2, q to quit]: 2

speech demo.mp4 (00:00:20)
Extract the audio as MP3? [y/N]: y
Extract screenshots? [y/N]: y
Seconds between screenshots [10]: 5
Extract a transcript? [y/N]: y
Include timestamps in the transcript? [Y/n]: y
Saving to D:\Videos\speech demo_mp4_extracted

== audio ==
size=     111KiB time=00:00:19.48 bitrate=  46.5kbits/s speed= 725x elapsed=0:00:00.02

== screenshots ==
frame=    5 fps=0.0 q=2.0 Lsize=N/A time=00:00:25.00 bitrate=N/A speed=70.4x elapsed=0:00:00.35

== transcript ==
Decoding audio...
Loading the Whisper 'tiny' model (the first run downloads it, which can take a few minutes)...
Transcribing...
Detected language: en (100% confidence)
  [ 14%] [00:00:00.000 --> 00:00:02.660] The Quick Brown Fox jumps over the lazy dog.
  [ 58%] [00:00:07.620 --> 00:00:11.280] Screen shots and audio are extracted with FFMPEG.
  [ 95%] [00:00:16.180 --> 00:00:18.580] Transcription uses the faster whisper library.

Done. Output folder: D:\Videos\speech demo_mp4_extracted
  audio        speech demo.mp3
  screenshots  screenshots_5s\ (5 images, every 5 s)
  transcript   speech demo_transcript.txt (3 lines, with timestamps)
```

Pressing Enter takes the default shown in capitals (or in brackets for the
interval); `q` quits at the list. Questions that cannot apply are skipped with
a note: no audio track means no audio or transcript, no video track means no
screenshots, and without faster-whisper the script prints the exact
`pip install` command instead of asking about a transcript. Before writing more
than 1,000 screenshots it asks you to confirm the interval.

## Output

```text
D:\Videos\
  talk.mp4
  talk_mp4_extracted\
    talk.mp3
    talk_transcript.txt
    screenshots_10s\
      shot_00-00-00.jpg
      shot_00-00-10.jpg
      ...
```

- The folder name includes the extension, so `talk.mp4` and `talk.mkv` never
  overwrite each other's results. `--output-dir DIR` puts it under `DIR`
  instead of beside the video.
- Each screenshot interval gets its own folder. Fractional intervals add
  milliseconds to the names (`shot_00-00-07.500.jpg`).
- Running again replaces files with the same names.
- A file only appears under its final name once it is complete. A step that
  fails or is cancelled with Ctrl+C removes its partial output, and one failed
  step does not stop the others.

A transcript has one line per spoken segment:

```text
[00:00:07.620 --> 00:00:11.280] Screenshots and audio are extracted with FFmpeg.
```

or, without timestamps, just the text.

## Flags

| Flag | Meaning |
| --- | --- |
| `DIRECTORY` | folder to scan (default: the current folder); subfolders are not searched |
| `--output-dir DIR` | where to create the `<name>_<ext>_extracted` folder (default: beside the video) |
| `--model NAME` | Whisper model: `tiny`, `base`, `small` (default), `medium`, `large-v3`, `turbo`, an English-only `.en` variant, or a local model folder; bigger is more accurate and slower |
| `--language CODE` | spoken language, such as `en` or `de` (default: detected automatically) |

Exit codes: `0` success or nothing selected, `1` a step failed, no videos were
found, or input ended early, `2` FFmpeg missing or not a folder, `130`
cancelled with Ctrl+C.

## How it works

- **Listing**: files with a video extension (`.mp4`, `.mkv`, `.mov`, `.webm`,
  `.avi`, `.wmv`, `.m4v`, `.mpg`, `.ts`, and others) directly in the folder,
  in natural order (`ep2` before `ep10`). Dotfiles, including macOS `._`
  files, are skipped. A `.ts` file is listed only if it really is an MPEG
  transport stream, because `.ts` is also TypeScript.
- **Audio**: ffmpeg re-encodes the default audio track to MP3 with LAME VBR
  quality 2 (about 190 kbit/s). Surround sound is downmixed to stereo.
- **Screenshots**: one ffmpeg pass with `fps=1/N:round=up`, which captures the
  frame on screen at exactly 0, N, 2N, ... seconds. ffmpeg's default rounding
  takes each frame about half an interval late. Anamorphic (non-square pixel)
  video is scaled to its display shape, and embedded cover art is ignored.
- **Transcript**: ffmpeg decodes the audio to 16 kHz mono, and faster-whisper
  transcribes it on the CPU (int8) with voice-activity detection to skip
  silence. With timestamps on, it also computes word-level timing, which keeps
  each line's start and end on the actual speech. Without it, a line can start
  seconds early, where the previous line ended.

ffmpeg does the decoding because faster-whisper 1.2.1's own decoder fails with
PyAV 19 (`av.open()` no longer accepts `metadata_errors`), and PyAV 19 is what
`pip install faster-whisper` installs today.

## Caveats

- Transcription runs on the CPU only. Long videos take a while, especially with
  larger models.
- The audio is always re-encoded to MP3, not copied from the original track.
  When a video has several audio tracks, ffmpeg's default choice (the one with
  the most channels) is used for both the MP3 and the transcript.
- Screenshots are high-quality JPEGs (`-q:v 2`). Interlaced video is not
  deinterlaced.

## Tests

```powershell
py -3 -m unittest discover -s .\tests -v
```

Media tests generate small clips with ffmpeg's built-in test sources and are
skipped when ffmpeg is missing. One of them encodes each second of a clip as a
gray level to prove that screenshot *k* shows the frame from *k* x interval
seconds. The real-Whisper test is opt-in because it downloads a model:

```powershell
$env:VIDEO_EXTRACT_LIVE_WHISPER = "1"   # needs faster-whisper; uses the tiny model
py -3 -m unittest discover -s .\tests -v
```
