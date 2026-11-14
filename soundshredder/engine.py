"""Bandit v2 adapter. This module is imported only inside the job subprocess."""

from __future__ import annotations

import gc
import os
import platform
import re
from collections.abc import Callable

import numpy as np

from .audio import STEMS

MODEL_RATE = 48000
BLOCK_SECONDS = 20
OVERLAP_SECONDS = 2
DEVICE_LABELS = {"cuda": "GPU", "mps": "Apple GPU", "cpu": "CPU"}


def apple_gpu_available() -> bool:
    """Metal (MPS) on Apple Silicon only; Intel Macs keep their tested CPU path."""
    import torch

    return platform.machine() == "arm64" and torch.backends.mps.is_available()


def move_runtime_to_mps(runtime) -> None:
    """bandit-infer refuses device="mps", so load on CPU and move the resident graph.

    Metal has no float64. The only float64 tensors are the 64 tiny mel
    ``freq_weights`` buffers; each product with a complex64 band mask is added
    straight into a complex64 mask, so casting them to float32 is numerically
    negligible (measured ~70 dB below the mixture versus CPU on an M2 Max).
    """
    import torch

    device = torch.device("mps")
    runtime.model.float().to(device).eval()
    runtime.handler.to(device).eval()
    runtime.device = device


def gpu_failure(exc: BaseException, device: str) -> str | None:
    """Return "memory" or "error" when a GPU failure can be retried on CPU, else None."""
    import torch

    text = str(exc).lower()
    if isinstance(exc, torch.cuda.OutOfMemoryError) or "out of memory" in text:
        return "memory"
    # Metal support in PyTorch is younger than CUDA's: also recover from unsupported
    # operators, Metal command-buffer errors and non-finite outputs. Other failures
    # (for example a model download error) would recur on CPU, so they are raised.
    if device == "mps" and (re.search(r"\bmps|\bmetal\b", text) or "returned invalid" in text):
        return "error"
    return None


def release_device_memory(device: str) -> None:
    import torch

    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    elif device == "mps":
        torch.mps.empty_cache()


def infer_active_windows(runtime, audio: np.ndarray, on_pass: Callable[[int, int], None]) -> dict[str, np.ndarray]:
    """Keep upstream padding/window math; skip windows cropped out of its final output.

    Bandit is stateless between windows. A window wholly outside [front_pad,
    front_pad + frames) cannot contribute to any exported sample. Accumulating
    just the intersecting samples also avoids keeping every prediction on GPU.
    The adapter is pinned; tests compare this against its native fold handler.
    """
    import torch

    handler = runtime.handler
    mixture = torch.from_numpy(np.ascontiguousarray(audio.T)).to(runtime.device)[None]
    with torch.inference_mode():
        windows, frames, _ = handler._pad_and_unfold(mixture)
        channels = audio.shape[1]
        left = handler.front_pad_samples
        right = left + frames
        chunk = handler.chunk_size_samples
        hop = handler.hop_size_samples
        indices = [i for i in range(windows.shape[1]) if i * hop < right and i * hop + chunk > left]
        accum = {name: torch.zeros((channels, frames), device=runtime.device) for name in runtime.model.stems}
        window = handler.scaled_window.reshape(1, chunk)
        for count, index in enumerate(indices, 1):
            prediction = runtime.model(
                {"mixture": {"audio": windows[:, index : index + 1].reshape(channels, 1, chunk)}}
            )
            start = index * hop
            lo, hi = max(start, left), min(start + chunk, right)
            for name in runtime.model.stems:
                values = prediction["estimates"][name]["audio"].reshape(channels, chunk)
                accum[name][:, lo - left : hi - left] += (values * window)[:, lo - start : hi - start]
            del prediction
            on_pass(count, len(indices))
        return {
            "speech": accum["speech"].T.cpu().numpy(),
            "music": accum["music"].T.cpu().numpy(),
            "effects": accum["sfx"].T.cpu().numpy(),
        }


def choose_device(requested: str) -> str:
    import torch

    if requested not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("Choose Auto, CPU, NVIDIA GPU, or Apple GPU.")
    if requested == "cuda" and not torch.cuda.is_available():
        raise ValueError(
            "NVIDIA GPU is unavailable. Choose CPU, or run Setup NVIDIA GPU.bat and update your NVIDIA driver."
        )
    if requested == "mps" and not apple_gpu_available():
        raise ValueError("Apple GPU (Metal) is unavailable. It needs an Apple Silicon Mac. Choose CPU instead.")
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if apple_gpu_available() else "cpu"


def run_model(audio: np.ndarray, device: str, progress: Callable[[float, str], None]) -> dict[str, np.ndarray]:
    """Bound accelerator memory using blocks and crossfade their overlapping edges."""
    import torch
    from bandit_infer import BanditSession

    torch.set_num_threads(max(1, min(8, (os.cpu_count() or 2) // 2)))
    block = BLOCK_SECONDS * MODEL_RATE
    overlap = OVERLAP_SECONDS * MODEL_RATE
    step = block - overlap
    starts = [0]
    while starts[-1] + block < len(audio):
        starts.append(starts[-1] + step)
    output = {name: np.zeros_like(audio) for name in STEMS}
    weights = np.zeros((len(audio), 1), dtype=np.float32)
    progress(0.08, "Loading Bandit v2. First use downloads the model (about 426 MB).")
    label = DEVICE_LABELS.get(device, device.upper())
    with BanditSession("v2-multi", device="cpu" if device == "mps" else device) as session:
        runtime = session._runtime
        if device == "mps":
            move_runtime_to_mps(runtime)
        for index, start in enumerate(starts):
            stop = min(start + block, len(audio))
            segment = audio[start:stop]

            def tick(counter, expected, index=index):
                fraction = (index + counter / expected) / len(starts)
                progress(
                    0.12 + 0.76 * fraction,
                    f"Separating on {label} · block {index + 1}/{len(starts)} · pass {counter}/{expected}",
                )

            prediction = infer_active_windows(runtime, segment, tick)
            window = np.ones((len(segment), 1), dtype=np.float32)
            if start:
                n = min(overlap, len(segment))
                window[:n, 0] *= np.linspace(0, 1, n, dtype=np.float32)
            if stop < len(audio):
                n = min(overlap, len(segment))
                window[-n:, 0] *= np.linspace(1, 0, n, dtype=np.float32)
            for stem in STEMS:
                values = prediction[stem]
                if values.shape != segment.shape or not np.isfinite(values).all():
                    raise RuntimeError(f"The model returned invalid {stem} samples.")
                output[stem][start:stop] += values * window
            weights[start:stop] += window
    if np.any(weights <= 0):
        raise RuntimeError("Audio overlap coverage failed.")
    for stem in STEMS:
        output[stem] /= weights
    release_device_memory(device)
    return output
