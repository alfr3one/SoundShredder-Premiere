"""Rebuild a video for editors: the original picture plus one audio track per stem.

Premiere Pro (and Resolve, Final Cut, Avid) put each audio stream of a QuickTime
file on its own timeline track, so dropping the result into a sequence gives
V1 + A1 Dialogue, A2 Music, A3 Sound effects, A4 Original mix.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .audio import LABELS, STEMS

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
OUTPUT_SUFFIX = " - Stems.mov"
TRACKS = (*STEMS, "original")
TRACK_NAMES = {**LABELS, "original": "Original mix"}


def ffmpeg(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    import imageio_ffmpeg

    return subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def streams(path: Path) -> list[str]:
    """Stream description lines, e.g. "Video: hevc (Main) (hvc1 / 0x31637668), ..."."""
    result = ffmpeg(["-i", str(path)], 60)
    return [
        line.split(": ", 1)[1]
        for line in result.stderr.splitlines()
        if line.strip().startswith("Stream #") and ": " in line
    ]


def video_codec(path: Path) -> str | None:
    """Codec of the first real video stream; cover art (attached pictures) does not count."""
    for stream in streams(path):
        if stream.startswith("Video:") and "attached pic" not in stream:
            return stream.split()[1].rstrip(",")
    return None


def audio_offset(path: Path) -> float:
    """Seconds from the file's start to its first decoded audio sample (normally 0).

    The stems are decoded from that sample onwards, so they are placed at this
    offset to stay in sync with the copied picture.
    """
    result = ffmpeg(
        ["-i", str(path), "-map", "0:a:0", "-af", "ashowinfo", "-frames:a", "1", "-f", "null", "-"], 120
    )
    match = re.search(r"pts_time:(-?[0-9.]+)", result.stderr)
    return max(0.0, float(match.group(1))) if match else 0.0


def unique_path(directory: Path, name: str) -> Path:
    path = directory / name
    stem, suffix = Path(name).stem, Path(name).suffix
    number = 2
    while path.exists():
        path = directory / f"{stem} ({number}){suffix}"
        number += 1
    return path


def output_name(filename: str) -> str:
    stem = Path(filename).stem.strip() or "video"
    return stem + OUTPUT_SUFFIX


def mux_multitrack(source: Path, tracks: dict[str, Path], destination: Path) -> dict:
    """Write ``destination`` (.mov): source picture, then 24-bit PCM tracks in TRACKS order.

    The picture is copied bit for bit. Only if QuickTime cannot hold the source's
    video codec (for example some WebM/AVI files) is it re-encoded as H.264.
    """
    codec = video_codec(source)
    if codec is None:
        raise ValueError("This file has no video stream.")
    offset = audio_offset(source)
    inputs = ["-i", str(source)]
    for name in TRACKS:
        inputs += ["-itsoffset", f"{offset:.6f}", "-i", str(tracks[name])]
    maps = ["-map", "0:v:0"] + [arg for index in range(len(TRACKS)) for arg in ("-map", f"{index + 1}:a:0")]
    metadata = ["-map_metadata", "0", "-map_chapters", "-1"]
    for index, name in enumerate(TRACKS):
        label = TRACK_NAMES[name]
        metadata += [
            f"-metadata:s:a:{index}",
            f"title={label}",
            f"-metadata:s:a:{index}",
            f"handler_name={label}",
            f"-disposition:a:{index}",
            "default" if index == 0 else "0",
        ]
    temporary = destination.with_name("." + destination.name + ".partial")
    attempts = [(["-c:v", "copy", *(["-tag:v", "hvc1"] if codec == "hevc" else [])], "copied")]
    attempts.append(
        (["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p"], "re-encoded as H.264")
    )
    errors = []
    try:
        for video_args, mode in attempts:
            result = ffmpeg(
                [
                    "-loglevel",
                    "error",
                    "-y",
                    *inputs,
                    *maps,
                    *video_args,
                    "-c:a",
                    "pcm_s24le",
                    *metadata,
                    "-f",
                    "mov",
                    str(temporary),
                ],
                3600,
            )
            if result.returncode == 0:
                video_mode = mode
                break
            errors.append(result.stderr.strip()[-600:])
        else:
            raise RuntimeError("Could not build the multitrack video. " + " | ".join(errors))
        written = streams(temporary)
        audio = [stream for stream in written if stream.startswith("Audio:")]
        if sum(stream.startswith("Video:") for stream in written) != 1 or len(audio) != len(TRACKS):
            raise RuntimeError("Multitrack video verification failed.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "file": str(destination),
        "name": destination.name,
        "tracks": [TRACK_NAMES[name] for name in TRACKS],
        "video": video_mode,
        "video_codec": codec,
        "audio_codec": "24-bit PCM",
        "audio_offset_seconds": round(offset, 6),
    }
