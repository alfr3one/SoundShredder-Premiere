import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pytest
import soundfile as sf

from soundshredder import video
from soundshredder.audio import decode


def make_video(path, *, audio_delay=0.0, codec=("libx264", "aac")):
    """Two seconds of test picture with a 440 Hz tone, optionally starting late."""
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=2",
            "-itsoffset",
            str(audio_delay),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=32000:duration=2",
            "-c:v",
            codec[0],
            "-c:a",
            codec[1],
            "-shortest" if not audio_delay else "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def stems_for(source, tmp_path):
    audio, rate = decode(source, tmp_path)
    tracks = {}
    for name, scale in zip(video.TRACKS, (0.5, 0.3, 0.2, 1.0), strict=True):
        tracks[name] = tmp_path / f"{name}.wav"
        sf.write(tracks[name], audio * scale, rate, subtype="PCM_24")
    return tracks, audio, rate


def first_audio_time(path, stream):
    result = video.ffmpeg(
        ["-i", str(path), "-map", f"0:a:{stream}", "-af", "ashowinfo", "-frames:a", "1", "-f", "null", "-"], 60
    )
    return float(result.stderr.split("pts_time:")[1].split()[0])


def test_multitrack_copies_picture_and_adds_one_track_per_stem(tmp_path):
    source = tmp_path / "clip.mp4"
    make_video(source)
    tracks, audio, rate = stems_for(source, tmp_path)
    before = source.read_bytes()
    info = video.mux_multitrack(source, tracks, tmp_path / video.output_name("clip.mp4"))
    assert source.read_bytes() == before
    assert info["name"] == "clip - Stems.mov" and info["video"] == "copied"
    assert info["tracks"] == ["Dialogue", "Music", "Sound effects", "Original mix"]
    lines = video.streams(tmp_path / info["name"])
    assert sum(line.startswith("Video: h264") for line in lines) == 1
    audio_lines = [line for line in lines if line.startswith("Audio:")]
    assert len(audio_lines) == 4 and all("pcm_s24le" in line and "32000 Hz" in line for line in audio_lines)
    assert not list(tmp_path.glob(".*partial"))
    # Each track holds the samples it was given, in order and at full length.
    for index, scale in enumerate((0.5, 0.3, 0.2, 1.0)):
        out = tmp_path / f"track{index}.wav"
        video.ffmpeg(["-y", "-i", str(tmp_path / info["name"]), "-map", f"0:a:{index}", str(out)], 60)
        values, out_rate = sf.read(out, dtype="float32", always_2d=True)
        assert out_rate == rate and len(values) == len(audio)
        assert np.max(np.abs(values - audio * scale)) < 1e-4


def test_late_audio_keeps_its_offset_against_the_picture(tmp_path):
    source = tmp_path / "late.mp4"
    make_video(source, audio_delay=0.5)
    # AAC priming means the first decoded sample is slightly before the tone.
    assert video.audio_offset(source) == pytest.approx(0.5, abs=0.05)
    tracks, _, rate = stems_for(source, tmp_path)
    info = video.mux_multitrack(source, tracks, tmp_path / "late - Stems.mov")
    for stream in range(4):
        out = tmp_path / f"track{stream}.wav"
        video.ffmpeg(["-y", "-i", str(tmp_path / info["name"]), "-map", f"0:a:{stream}", str(out)], 60)
        values, _ = sf.read(out, dtype="float32", always_2d=True)
        onset = int(np.argmax(np.abs(values[:, 0]) > 1e-3))
        # The tone starts 0.5 s after the first picture frame, exactly as in the source.
        assert first_audio_time(tmp_path / info["name"], stream) + onset / rate == pytest.approx(0.5, abs=0.002)


def test_codecs_quicktime_cannot_hold_are_reencoded(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    make_video(source)
    tracks, _, _ = stems_for(source, tmp_path)
    real = video.ffmpeg

    def refuse_copy(args, timeout):
        if "copy" in args:
            return subprocess.CompletedProcess(args, 1, "", "codec not currently supported in container")
        return real(args, timeout)

    monkeypatch.setattr(video, "ffmpeg", refuse_copy)
    info = video.mux_multitrack(source, tracks, tmp_path / "clip - Stems.mov")
    assert info["video"] == "re-encoded as H.264"


def test_audio_only_files_are_not_videos(tmp_path):
    source = tmp_path / "tone.wav"
    sf.write(source, np.zeros((800, 1)), 8000)
    assert video.video_codec(source) is None


def test_output_names_never_overwrite(tmp_path):
    (tmp_path / "a - Stems.mov").touch()
    assert video.unique_path(tmp_path, "a - Stems.mov").name == "a - Stems (2).mov"
    assert video.output_name("مشهد ١.mp4") == "مشهد ١ - Stems.mov"


def test_worker_builds_the_multitrack_video_for_a_watched_file(tmp_path, monkeypatch):
    from soundshredder import worker
    from soundshredder.audio import STEMS, read_json, write_json

    watched = tmp_path / "watched"
    watched.mkdir()
    source = watched / "scene.mp4"
    make_video(source)
    job = tmp_path / "job"
    job.mkdir()
    write_json(
        job / "request.json",
        {
            "filename": "scene.mp4",
            "source": "scene.mp4",
            "source_path": str(source),
            "device": "cpu",
            "gains": dict(speech=1, music=0, effects=1),
            "export_dir": str(watched / "SoundShredder Stems"),
        },
    )
    monkeypatch.setattr(worker, "choose_device", lambda _: "cpu")
    monkeypatch.setattr(
        worker, "run_model", lambda audio, device, progress: {stem: audio / 3 for stem in STEMS}
    )
    worker.process(job)
    report = read_json(job / "separation.json")
    assert report["video"]["file"] == str(watched / "SoundShredder Stems" / "scene - Stems.mov")
    assert (watched / "SoundShredder Stems" / "scene - Stems.mov").is_file()
    assert not (job / "source.mp4").exists()  # read in place, not copied
    progress = read_json(job / "progress.json")
    assert progress["status"] == "complete" and "scene - Stems.mov" in progress["message"]


def test_worker_saves_stems_next_to_the_footage_for_premiere(tmp_path, monkeypatch):
    from soundshredder import worker
    from soundshredder.audio import STEMS, read_json, write_json

    footage = tmp_path / "footage"
    footage.mkdir()
    source = footage / "shot.v2.mp4"
    make_video(source, audio_delay=0.5)
    job = tmp_path / "job"
    job.mkdir()
    export = footage / "SoundShredder Stems"
    (export / "shot.v2 - Stems").mkdir(parents=True)  # an earlier version is kept
    write_json(
        job / "request.json",
        {
            "filename": "shot.v2.mp4",
            "source": "shot.v2.mp4",
            "source_path": str(source),
            "device": "cpu",
            "gains": dict(speech=1, music=0, effects=1),
            "export_dir": str(export),
            "export_video": False,
            "export_stems": True,
            "origin": "premiere",
        },
    )
    monkeypatch.setattr(worker, "choose_device", lambda _: "cpu")
    monkeypatch.setattr(worker, "run_model", lambda audio, device, progress: {stem: audio / 3 for stem in STEMS})
    worker.process(job)
    report = read_json(job / "separation.json")
    assert "video" not in report and not list(export.glob("*.mov"))
    stems = report["stems_export"]
    assert stems["folder"] == str(export / "shot.v2 - Stems (2)")
    assert sorted(Path(path).name for path in stems["files"].values()) == [
        "shot.v2 - Dialogue.wav",
        "shot.v2 - Music.wav",
        "shot.v2 - SFX.wav",
    ]
    assert stems["audio_offset_seconds"] == pytest.approx(0.5, abs=0.05)
    info = sf.info(stems["files"]["speech"])
    assert info.subtype == "PCM_24" and info.frames == read_json(job / "source.json")["frames"]
