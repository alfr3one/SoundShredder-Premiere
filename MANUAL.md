# SoundShredder manual

A local Python audio tool for cleaning up Seedance clips and other mixed soundtracks. Upload one file, separate **dialogue, music and sound effects**, then choose what stays. Includes a browser interface and a command line tool.

**Made by cyr4x · Made for Higgsfield Community.** [X](https://x.com/_cyr4x) · [Higgsfield](https://higgsfield.ai/@cyr4x) · [Instagram](https://www.instagram.com/__cyr4x__/) · [YouTube](https://www.youtube.com/@cyr4xfilms). The interface includes the Higgsfield mark and Space Grotesk typography, bundled locally with the font license; see `static/BRANDING.md` for asset sources.

## Start on Windows

1. Install **Python 3.12** from [python.org](https://www.python.org/downloads/) with **Add Python to PATH** enabled.
2. Double-click **Start SoundShredder.bat**. First launch installs dependencies in this folder's `.venv` and opens [the local app](http://127.0.0.1:7860).
3. Drop in an audio file, choose a preset, and click **Separate audio**.
4. Listen to each track and the cleaned mix. Adjust levels, click **Update mix**, then download a WAV or ZIP containing all tracks.

Keep the launcher window open while using the app. Close it or press Ctrl+C to stop the server; active processing is cancelled on orderly shutdown.

**Remove music** is the default: it keeps dialogue and effects together. Other presets keep only dialogue, keep only effects, remove effects, or remove dialogue. Every run saves all three stems. Setting every slider to zero produces a silent file of the same duration.

## Start on Mac

Download and extract **SoundShredder-Mac.zip**, then move the entire extracted folder to Documents or another writable location.

1. Install **Python 3.11** with the [macOS universal2 installer](https://www.python.org/downloads/release/python-3119/).
2. Double-click **Start SoundShredder.command**. First launch detects Apple Silicon or Intel, installs the matching dependencies, and opens the same browser interface.
3. Upload a file and separate it using **Auto** (the Apple GPU on Apple Silicon), **Apple GPU**, or **CPU**. Keep the Terminal window open; press Control+C to stop.

Requires **macOS 12 or newer**. On Apple Silicon, **Auto** uses the **Apple GPU (Metal)** and finishes on CPU if the GPU fails; choose **CPU** to skip the GPU. Intel Macs use CPU. Apple Silicon supports Python 3.10–3.13; Intel supports Python 3.10–3.11. Python 3.11 works for both architectures. Use native Python rather than Rosetta.

Intel setup pins [PyTorch 2.2.2](https://pypi.org/project/torch/2.2.2/) and NumPy 1.26.4; Apple Silicon uses [PyTorch 2.8.0](https://pypi.org/project/torch/2.8.0/). Intel also needs Python 3.11 or older because the model's disabled compiler calls encounter [PyTorch 2.2's Python 3.12 guard](https://github.com/pytorch/pytorch/blob/v2.2.2/torch/__init__.py#L1638-L1640). The ZIP includes **START HERE - MAC.txt** with first-launch, permission, certificate, and recovery instructions. Python is installed separately; model weights download on first separation. No Homebrew or separate FFmpeg installation is needed.

If Finder will not open the launcher, open Terminal, type `bash `, drag **Start SoundShredder.command** into Terminal, and press Return.

### Mac app (optional)

After the first launch has created `.venv`, run `bash mac-app/build_app.sh` to build **SoundShredder.app** and install it in Applications (needs Xcode or the Command Line Tools). The app shows SoundShredder in its own window, with no Terminal or browser. It starts the engine from this folder, saves downloads to Downloads and shows them in Finder, and stops the engine when you quit. If SoundShredder is already running from the launcher, the app uses it and leaves it running.

- The app remembers where this folder was when it was built. After moving the folder, run the build script again, or choose the folder when the app asks for it.
- On an external drive, macOS asks whether SoundShredder may access files on a removable volume the first time it opens. Click **Allow**; the window says when it is waiting for this. macOS may also ask once before saving to Downloads. If access was refused, turn it on in System Settings › Privacy & Security › Files & Folders › SoundShredder.
- **File › Show Server Log** opens the engine log (`~/Library/Logs/SoundShredder/server.log`). **File › Open in Browser** opens the same page in your browser.

## Watch a folder (multitrack video for Premiere)

Scroll to **04 — Watch a folder** (or click **Watch folder** in the sidebar), choose a folder and click **Start watching**. Every new video added to that folder is separated automatically and saved as **`<name> - Stems.mov`** in a **SoundShredder Stems** folder inside it (or another folder you choose). Each file contains:

| Track | Content |
| --- | --- |
| V1 | The original picture, copied bit for bit (re-encoded to H.264 only if QuickTime cannot hold the source codec) |
| A1 | Dialogue |
| A2 | Music |
| A3 | Sound effects |
| A4 | Original mix, for reference |

Audio is 24-bit PCM at the source sample rate, placed at the source's original audio offset, so it stays in sync with the picture. Import the .mov into Premiere Pro and drag it to a sequence: each layer lands on its own audio track.

- Only the top level of the folder is watched. Videos already there are left alone unless you tick **Also separate videos already in the folder**. Hidden files and `- Stems.mov` outputs are ignored.
- A video is queued once it has stopped growing for a few seconds (and Finder has finished copying it), then separated one at a time on the device chosen in step 02.
- Keep SoundShredder open while watching. The watch setting and queue are saved; on the next launch, videos added while it was closed are separated and interrupted ones restart.
- Watched videos are read in place, not copied. Each also gets a normal session (listed as **AUTO**) where you can listen, remix and download stems. The 10-minute limit still applies.
- Uploaded videos get the same multitrack file: **Multitrack video (.mov)** in the results.

## Premiere Pro panel

A panel inside Premiere Pro 2022 or later that separates clips straight from the timeline.

**On Windows**, double-click `premiere-panel\install.bat` instead of running the script below (it links the panel into `%APPDATA%\Adobe\CEP\extensions` and sets `PlayerDebugMode` in the registry; no administrator rights needed), then restart Premiere Pro. `uninstall.bat` removes it. Windows support has not yet been run on a Windows PC.

**On a Mac:**

Run this once, then restart Premiere Pro and open **Window › Extensions › SoundShredder**:

```bash
bash premiere-panel/install.sh
```

The script links the `premiere-panel` folder into `~/Library/Application Support/Adobe/CEP/extensions` and allows Premiere to load unsigned panels (`PlayerDebugMode`). Run it again after moving the SoundShredder folder. `premiere-panel/uninstall.sh` removes the link.

**Use:**

1. Keep the SoundShredder app open. The panel finds it on its own and shows **Connected**.
2. Select one or more clips in the timeline and click **Separate selected clips**.
3. Each clip is separated in turn. When it finishes, Dialogue, Music and SFX are placed on three neighbouring audio tracks that are empty under the clip (new tracks are added at the bottom when needed), trimmed to the clip's in and out points and in sync with it. The stems go into a **SoundShredder** bin.
4. **Original audio** sets what happens to the clip's own sound:
   - **Replace, link stems** (default): the clip's audio is removed first, so the stems usually land where it was (A1–A3), and they are linked to the clip's picture. The clip then moves, trims and deletes as one, like the original but with three audio tracks. Audio on a locked track is left in place. If a stem cannot be placed, **Edit > Undo** brings the original audio back.
   - **Mute:** the original audio stays on its track, switched off; the stems go on three free tracks and are not linked.
   - **Keep:** the original audio is left as it is.

- The whole source file is separated once, so separating the same footage again (another cut of it, or after undo) is instant. The 10-minute limit applies to the source file.
- Stems are saved next to the footage in `SoundShredder Stems/<name> - Stems/` as `<name> - Dialogue.wav`, `- Music.wav` and `- SFX.wav` (in SoundShredder's data folder if the footage folder is read-only). **Show stems in Finder** (Explorer on Windows) opens the folder.
- Clips with a speed change or reversed, nested sequences, and other non-file items are skipped and listed. Locked tracks are never used.
- **Import new multitrack videos into this project** brings each finished `- Stems.mov` from the watch folder into the SoundShredder bin of the open project.
- The panel and the app talk only on this computer (127.0.0.1). The panel needs the current SoundShredder: if it says the app is too old, quit and reopen SoundShredder.

## CPU and GPU

- **Auto:** NVIDIA CUDA if available, then the Apple GPU (Metal) on Apple Silicon Macs; CPU otherwise.
- **NVIDIA GPU:** faster, with an up-to-date NVIDIA driver. The provided installer uses PyTorch 2.8 with CUDA 12.8, including support for RTX 50-series cards. Its first download is about 3.2 GB. A separate CUDA Toolkit installation is not needed.
- **CPU:** works without a GPU. It can take several minutes even for short clips. Use **Setup CPU.bat** to install a smaller CPU-only runtime.
- **Setup NVIDIA GPU.bat** switches an existing CPU installation to CUDA. A CUDA installation can still process on CPU using the app selector.
- **Use CPU if GPU memory runs out** retries a failed GPU run on CPU. Uncheck it to surface the GPU error instead. Unavailable explicitly selected GPUs give an error rather than silently changing devices.
- **Apple GPU (Metal):** Apple Silicon only. bandit-infer refuses `mps`, so SoundShredder loads the model on CPU and then moves it to Metal, casting 64 tiny float64 frequency-weight buffers to float32 (Metal has no float64). On an M2 Max this measured about 3–5× faster than CPU per inference window, with stems differing from CPU by about 70 dB below the mix. If Metal fails or runs out of memory and **Use CPU if GPU memory runs out** is checked, the job finishes on CPU and the report records a warning; the GPU error is kept in that session's `worker.log`.
- AMD GPU acceleration (including Intel Macs with AMD graphics) is not implemented; use CPU on those machines.

First separation downloads the official Bandit v2 weights (~426 MB) into `~/.cache/bandit-infer`. Subsequent runs work offline. No uploaded audio is sent to a cloud service.

## Formats and files

WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, WMA and common video containers are supported. Videos also yield a multitrack .mov (picture plus one audio track per layer); the source video is never modified. FFmpeg is bundled through `imageio-ffmpeg`.

Limits: **500 MB, 10 minutes, mono or stereo, 8–192 kHz**. Surround input is rejected instead of silently downmixed. The decoded source's sample rate, frame count and channel layout are restored after inference. Compressed codecs can contain padding, so “source length” means the decoded audio length.

Downloads are 24-bit PCM WAV. The local `data/<session-id>/` folder contains the uploaded copy, full-precision stems, exports, processing report, and a worker log. Original files are never overwritten. Sessions survive a restart; unfinished sessions are marked stopped. **Delete this session** removes its local uploaded copy and generated outputs. Files remain on disk until you delete them; each updated mix adds a new WAV/ZIP revision. Move downloads elsewhere before deleting a session.

The web server binds to `127.0.0.1` only and does not provide public hosting or authentication. Run one server per data directory. Change the location with `SOUNDSHREDDER_DATA` if needed.

## What is improved around Bandit v2

This uses the existing pretrained weights; it does **not** claim to retrain or improve the underlying model's source recognition.

- Bounded 20-second inference blocks, 2-second overlapping crossfades, and the model's native internal 8-second / 1-second overlap handler reduce memory growth and smooth block boundaries. Blocked inference may differ slightly from processing the entire clip in one call.
- The optimized inference runner skips padded windows that cannot contribute to any exported sample, and accumulates predictions directly into the output. It retains upstream padding and window weighting. Deterministic padding/fold tests and a real-weight parity check guard this optimization; it changes compute scheduling, not the model's weights.
- Explicit 48 kHz resampling for the model, followed by restoration of source rate and exact frame count.
- Mono/stereo preservation and one shared output gain across all stems, leaving headroom for any mix with track levels from 0–100%. No independent normalization that unexpectedly boosts noise or changes relative levels.
- Isolated cancellable processing, real inference progress, serialized GPU jobs, and optional CPU retry after GPU out-of-memory errors.
- Full-precision stems let you remix repeatedly without rerunning the model or accumulating 24-bit quantization.
- A report verifies source SHA-256, frames, sample rate, channels and output peak. Residual RMS and relative stem levels aid inspection; they do **not** measure audible separation quality. The residual is not added back, since it may contain the sound you removed.

### Real limitations and possible next improvements

The model can confuse breaths, singing, rhythmic impacts and background music. Effects includes ambience. It cannot selectively remove one unwanted whoosh while preserving every other effect in the same stem. Always audition important dialogue and quiet effects. Returning three tracks with matching lengths proves routing/timing, not perceptual quality.

Useful further work would be timed cleanup regions; a text-conditioned sound separator for one named unwanted sound; and an optional second model for comparison. A genuinely better Bandit model would require a licensed, labelled test/training set of synthetic video audio with known dialogue/music/effects stems, followed by fine-tuning and blind listening tests. Evaluate bleed, missing speech, effects preservation, transient artifacts and multilingual speech independently before claiming improvement. Mixture consistency alone can restore the very noise you wanted removed.

## Manual installation / other operating systems

Use Python 3.10–3.13, or 3.10–3.11 on Intel Macs. Python 3.12 is recommended for Windows/Linux; Python 3.11 works on both Mac architectures. Platform-specific setup is provided for Windows, Linux and macOS; CUDA requires a supported NVIDIA environment. See `VERIFICATION.md` for actual test coverage.

```shell
python setup_runtime.py --device cpu
# Windows:
.venv\Scripts\python.exe app.py
# macOS / Linux:
.venv/bin/python app.py
```

For NVIDIA use `python setup_runtime.py --device cuda`. Optional flags: `app.py --no-browser --port 7860`.

```shell
python -m soundshredder.cli "clip.wav" --device auto --keep speech effects
python -m soundshredder.cli "clip.wav" --device cpu --keep speech --output exports
python -m soundshredder.cli "clip.mp4" --device cuda --keep effects --no-cpu-fallback
```

Run CLI commands with the `.venv` Python or activate that environment first. Each run creates a unique session folder under the output directory, including its inputs and reports.

## Development and verification

```shell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
```

Tests use small deterministic fixtures for routing, resampling, mix levels, silence, API errors, file safety and the inference block crossfade. They do not substitute for model listening tests. Hardware smoke results, when present, are documented in `VERIFICATION.md`.

## Model attribution

- [Bandit v2 research implementation](https://github.com/kwatcharasupat/bandit-v2) by Karn Watcharasupat and collaborators; [paper](https://arxiv.org/abs/2407.07275).
- [bandit-infer](https://github.com/openmirlab/bandit-infer) packages the inference graph and verifies the official checkpoint. This app pins its source revision for reproducibility.
- Upstream code is Apache-2.0. The official v2 checkpoint is distributed under **CC BY-SA 4.0**, as documented by the adapter and its [Zenodo source](https://zenodo.org/records/12701995). The checkpoint is downloaded separately, not bundled here.
