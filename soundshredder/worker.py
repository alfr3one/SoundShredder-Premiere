"""An isolated, cancellable separation process with atomic progress updates."""

from __future__ import annotations

import argparse
import gc
import shutil
import time
import traceback
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import STEMS, decode, export_mix, read_json, resample, sha256, waveform, write_json
from .engine import (
    BLOCK_SECONDS,
    DEVICE_LABELS,
    MODEL_RATE,
    OVERLAP_SECONDS,
    choose_device,
    gpu_failure,
    release_device_memory,
    run_model,
)
from .video import audio_offset, mux_multitrack, output_name, unique_path, video_codec

STEM_FILE_LABELS = {"speech": "Dialogue", "music": "Music", "effects": "SFX"}


def process(job_dir: Path) -> None:
    started = time.monotonic()
    request = read_json(job_dir / "request.json")

    def progress(amount: float, message: str, **extra):
        write_json(job_dir / "progress.json", {"status": "running", "progress": amount, "message": message, **extra})

    progress(0.02, "Reading your audio and checking its duration.")
    work = job_dir / "work"
    output = job_dir / "output"
    work.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    # Watched-folder jobs read the video where it is instead of copying it.
    source = Path(request["source_path"]) if request.get("source_path") else job_dir / request["source"]
    original_hash = sha256(source)
    audio, rate = decode(source, work)
    sf.write(output / "original.wav", audio, rate, subtype="FLOAT")
    source_info = {
        "filename": request["filename"],
        "sample_rate": rate,
        "frames": len(audio),
        "channels": audio.shape[1],
        "duration": len(audio) / rate,
        "waveform": waveform(audio),
    }
    write_json(job_dir / "source.json", source_info)
    model_audio = resample(audio, rate, MODEL_RATE)
    selected = choose_device(request["device"])
    warnings = []
    try:
        stems = run_model(model_audio, selected, progress)
    except Exception as exc:
        failure = gpu_failure(exc, selected) if selected in {"cuda", "mps"} else None
        if failure is None or not request.get("cpu_fallback", True):
            raise
        # Keep the GPU error in worker.log, then release the failed frame's model
        # references before retrying on CPU.
        traceback.print_exc()
        traceback.clear_frames(exc.__traceback__)
        gc.collect()
        release_device_memory(selected)
        label = DEVICE_LABELS[selected]
        if failure == "memory":
            warnings.append(f"{label} ran out of memory. Completed on CPU instead.")
            progress(0.08, f"{label} memory is full. Retrying on CPU; this will take longer.")
        else:
            warnings.append(f"{label} could not process this file ({str(exc)[:160]}). Completed on CPU instead.")
            progress(0.08, f"{label} could not process this file. Retrying on CPU; this will take longer.")
        selected = "cpu"
        stems = run_model(model_audio, selected, progress)
    progress(0.90, "Restoring the source sample rate and exporting your tracks.")
    stems = {key: resample(stems[key], MODEL_RATE, rate, len(audio)) for key in STEMS}
    # A common gain preserves relative stem levels and leaves headroom for every
    # possible mix with sliders in [0, 1], without clipping or per-stem boosts.
    bound = np.zeros_like(audio)
    for values in stems.values():
        bound += np.abs(values)
    peak_bound = float(np.max(bound))
    gain = min(1.0, 0.98 / max(peak_bound, 1e-9))
    for name, values in stems.items():
        sf.write(work / f"{name}.wav", values, rate, subtype="FLOAT")
        sf.write(output / f"{name}.wav", values * gain, rate, subtype="PCM_24")
        info = sf.info(output / f"{name}.wav")
        if (info.frames, info.samplerate, info.channels) != (len(audio), rate, audio.shape[1]):
            raise RuntimeError(f"Timing verification failed for {name}.")
    if sha256(source) != original_hash:
        raise RuntimeError("Source integrity verification failed.")
    residual = audio - sum(stems.values())
    source_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    residual_rms = float(np.sqrt(np.mean(residual.astype(np.float64) ** 2)))
    diagnostics = {
        "source_rms": source_rms,
        "reconstruction_residual_rms": residual_rms,
        "residual_to_source_db": round(float(20 * np.log10(max(residual_rms, 1e-12) / max(source_rms, 1e-12))), 2),
        "stem_rms": {name: float(np.sqrt(np.mean(values.astype(np.float64) ** 2))) for name, values in stems.items()},
        "interpretation": "Residual measures how closely the stems sum to the input, not separation quality. It is not added back because it may contain removed sounds.",
    }
    report = {
        **source_info,
        "source_sha256": original_hash,
        "source_unchanged": True,
        "model": "Bandit v2 multi",
        "model_sample_rate": MODEL_RATE,
        "inference": {
            "adapter_revision": "7ec03cb568811958db65a96a10fdb8879922b2ac",
            "block_seconds": BLOCK_SECONDS,
            "block_overlap_seconds": OVERLAP_SECONDS,
            "skip_noncontributing_padded_windows": True,
        },
        "requested_device": request["device"],
        "device": selected,
        "output_gain": gain,
        "output_format": "24-bit PCM WAV",
        "processing_seconds": round(time.monotonic() - started, 2),
        "warnings": warnings,
        "diagnostics": diagnostics,
        "notes": [
            "The source is never overwritten. Output length, sample rate and channels match the decoded source.",
            "AI separation may leave bleed or change quiet effects and breaths. Listen before publishing.",
            "The effects track includes ambience. Individual noises within a track are not separately selectable.",
        ],
    }
    export_dir = request.get("export_dir")
    message = "Your tracks are ready. Listen, adjust, and download."
    codec = video_codec(source)
    if codec and request.get("export_video", True):
        progress(0.94, "Building the multitrack video for Premiere.")
        try:
            directory = Path(export_dir) if export_dir else output
            directory.mkdir(parents=True, exist_ok=True)
            tracks = {stem: output / f"{stem}.wav" for stem in STEMS} | {"original": output / "original.wav"}
            report["video"] = mux_multitrack(source, tracks, unique_path(directory, output_name(request["filename"])))
            message = f"Multitrack video saved: {report['video']['name']}"
        except Exception as exc:
            # A watched-folder job exists to deliver this video; an uploaded one still has its stems.
            if export_dir:
                raise
            traceback.print_exc()
            warnings.append(f"The multitrack video could not be built ({str(exc)[:200]}). The audio tracks are fine.")
    elif export_dir and not request.get("export_stems"):
        raise ValueError("This file has no video stream, so no multitrack video was made.")
    if request.get("export_stems") and export_dir:
        # Stems for editors: placed next to the footage so the project never points into SoundShredder's data.
        progress(0.96, "Saving the stems next to your footage.")
        base = Path(request["filename"]).stem.strip() or "clip"
        folder = Path(export_dir) / f"{base} - Stems"
        number = 2
        while folder.exists():
            folder = Path(export_dir) / f"{base} - Stems ({number})"
            number += 1
        folder.mkdir(parents=True)
        files = {}
        for stem in STEMS:
            files[stem] = str(folder / f"{base} - {STEM_FILE_LABELS[stem]}.wav")
            shutil.copyfile(output / f"{stem}.wav", files[stem])
        report["stems_export"] = {
            "folder": str(folder),
            "files": files,
            # Stem sample 0 sits this far after the media's start (its first video frame).
            "audio_offset_seconds": round(audio_offset(source), 6) if codec else 0.0,
            "duration": len(audio) / rate,
        }
        message = f"Stems saved in {folder.name}"
    if sha256(source) != original_hash:
        raise RuntimeError("Source integrity verification failed.")
    write_json(job_dir / "separation.json", report)
    export_mix(job_dir, request["gains"])
    write_json(
        job_dir / "progress.json",
        {"status": "complete", "progress": 1, "message": message, "device": selected},
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    try:
        process(args.job_dir)
    except Exception as exc:
        traceback.print_exc()
        message = str(exc) or type(exc).__name__
        if "out of memory" in message.lower():
            message = "There is not enough memory. Try CPU mode, close other GPU apps, or use a shorter clip."
        write_json(args.job_dir / "progress.json", {"status": "failed", "progress": 0, "message": message[:1200]})
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
