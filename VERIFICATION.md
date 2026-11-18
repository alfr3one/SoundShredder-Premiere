# Verification on this computer

## Premiere panel: stems replace the clip's audio and are linked to it, on the same M2 Max

- **Added:** an **Original audio** choice in the panel: **Replace, link stems** (the new default), **Mute** (the old behaviour) and **Keep**. Replace unlinks the clip's picture from its own audio, removes that audio, places Dialogue, Music and SFX on the first free run of three tracks (so usually A1–A3) and links each clip's picture to its three stems with `Sequence.linkSelection()`.
- **Timeline logic, in a simulated Premiere:** the real `host/soundshredder.jsx` ran in Node against a stand-in for Premiere's ExtendScript objects; 14 scenarios passed. Checked: stems on A1–A3 with the original removed and the picture linked to exactly the three stems; a stereo pair on A1+A2 removed while a music bed and an unselected cut of the same footage stayed (the cut still linked to its own audio); a busy A2 pushing the stems to A3–A5; two cuts of one source each linked to their own stems; an audio-only clip's three stems linked together; audio on a locked track left in place with a warning; a warning (stems still placed) when linking is refused; stems shorter than the clip; Mute and Keep unchanged; the old `muteOriginal` flag still honoured. The replace scenarios also passed with a stand-in where removing one linked item removes the whole linked group, so the picture survives even then.
- **Panel:** `panel.js` and the host script parse; the host script uses only ES3 syntax. The controls fit at 320 px and at the 240 px minimum width without overflow (checked with the panel's stylesheet in a browser; the panel's script itself was not run there).
- **Not checked:** none of this ran in Premiere Pro itself. That Premiere 26's `unlinkSelection`, `TrackItem.remove` and `linkSelection` behave like the stand-in is an assumption until the panel is tried on a real timeline.

## Windows support, added on the same M2 Max (not yet run on Windows)

- **Added:** `Start SoundShredder.bat`, `Setup CPU.bat` and `Setup NVIDIA GPU.bat` (sharing `scripts/windows-python.bat` to find Python 3.10–3.13), the panel installer `premiere-panel/install.bat` and `uninstall.bat` (a directory junction into `%APPDATA%\Adobe\CEP\extensions` plus `PlayerDebugMode` in the registry), a Windows folder picker in the app (PowerShell's FolderBrowserDialog, with the prompt passed through the environment), and panel buttons that use Explorer and `Start SoundShredder.bat` on Windows.
- **Checked here:** new API tests for the folder picker on macOS and Windows (path trimming, a drive root, cancel, unsupported systems); 79 tests passed and 3 were skipped, and Ruff passed. The panel loaded in jsdom without errors and, through the installed extension link, found the real SoundShredder folder and its launcher. The batch files are ASCII with CRLF line endings.
- **Not checked:** none of the Windows files has been run on a Windows PC, and the panel has not been loaded in Premiere Pro for Windows.

## Premiere Pro panel, added on the same M2 Max

- **Server side, with the real model** (separate test server and data folder, Apple GPU): two clips sent through `POST /api/clips` were queued and separated one after the other (about 42 s and 90 s). The three stems landed in `SoundShredder Stems/<name> - Stems/` next to the footage, one under an Arabic name. Asking again for the same unchanged file reused the finished job at once.
- **Timeline logic, in a simulated Premiere:** the real `host/soundshredder.jsx` ran in Node against a stand-in for Premiere's ExtendScript objects. Checked: one entry per selected clip instance (linked video and audio counted once), speed-changed and nested items skipped, the first free run of three tracks chosen (busy and locked tracks passed over, missing tracks added), stems trimmed to each instance's in and out points with the audio offset applied, original audio muted only for the separated clips, files not imported twice, watch-folder import.
- **Panel end to end, simulated Premiere:** the real `panel.js` ran in jsdom, talking to the real test server and the simulated Premiere. Both selected clips went from Sending to Done; their stems sat on A2–A4 at the clips' timeline positions and the A1 originals were muted. Arabic names reached ExtendScript escaped to ASCII. The panel layout was checked at 320 px wide.
- **In Premiere Pro 2026 (26.0):** the user installed the panel and reported that it works. The automated checks above did not drive Premiere itself, so the fallbacks for a different Premiere version (in/out points in seconds, an error if tracks cannot be added, a warning if a clip cannot be muted) were checked only in the simulation.

## Watch folder and multitrack video, added on the same M2 Max

- **End to end through the web UI** (separate test server and data folder, Apple GPU): watching was started from the new panel, then a real 12-second Seedance clip (HEVC 2048×1152, AAC 32 kHz stereo) was copied in under an Arabic file name. It was detected, queued once stable, separated, and `<name> - Stems.mov` appeared in `SoundShredder Stems` about 45 s after the copy.
- **Output checks:** one HEVC video stream copied unchanged (`hvc1`), four 24-bit PCM stereo tracks named Dialogue, Music, Sound effects and Original mix, all 386,048 samples long. The Original mix track matched the source's decoded audio to 1.2e-7; the three stems summed to the source with a residual of −56.8 dB RMS and zero samples of lag. Download (`/api/jobs/<id>/video`) returned the file with its UTF-8 name. The page reported no JavaScript errors.
- **Sync with a late audio start:** a test video whose audio starts 0.5 s after the picture keeps the tone at 0.5 s ± 2 ms on every output track (AAC priming included).
- **Tests:** 76 collected; 73 passed and 3 were skipped. New tests cover the mux (track order, levels, re-encode fallback, offset), the watcher (baseline, settling while a file grows, one-at-a-time queue, pause/resume, retry, restart requeue, invalid settings), the worker writing the .mov for a watched file without copying it, and the watch API. Ruff passed.
- **Not exercised:** the macOS folder picker dialog (its AppleScript compiles), Show in Finder, importing the .mov into Premiere Pro itself, Windows/Linux.

## Mac app, added on the same M2 Max

`mac-app/` builds **SoundShredder.app**, a native window (Swift, AppKit and WebKit) around the same local server. It was built with Swift 6.3.3 against the macOS 26.5 SDK, ad-hoc signed, and installed in /Applications, with the SoundShredder folder on an external drive.

- **Start-up:** opening the app started `.venv/bin/python app.py --no-browser --port 7860` from the SoundShredder folder as a child of the app. The engine answered in about 6 seconds. Its log shows the window loading the page, which then requested `/api/system` and `/api/jobs`, so the page's script ran in the window.
- **Problem found on the first launch, and fixed:** macOS held the engine's first read of the external drive until the user answered its "access files on a removable volume" prompt, and the app could not see that wait. After 180 seconds it reported a failure. Try Again then started a second engine that lost the port, and quitting left the first engine running. Now the app reads its folder itself before starting the engine and says when it is waiting for this permission. It never gives up while the engine is running, Try Again goes back to waiting for the existing engine, and a SoundShredder that took the port first is used instead. The rebuilt app started without a new prompt; the permission given to the first build still applied.
- **Not exercised:** the permission-waiting and permission-refused screens (access was already granted), picking a file in the window, downloading through the window, and stopping the engine on quit. Separation itself is unchanged from the Apple GPU tests below.

## Apple GPU (Metal), added on an Apple M2 Max

Added after the original package and tested on an Apple M2 Max (30-core GPU, 32 GB) with macOS 26.5.1, Python 3.12.13 and PyTorch 2.8.0 (CPU wheel from PyPI). Other heavy apps were running during the measurements, so CPU times are higher than on an idle Mac.

- **Device selection:** Auto picks NVIDIA CUDA, then the Apple GPU on Apple Silicon only, then CPU. Intel Macs keep CPU even if PyTorch reports MPS for AMD graphics. bandit-infer refuses `device="mps"`, so the model is loaded on CPU and moved to Metal; its 64 tiny float64 `freq_weights` buffers are cast to float32 because Metal has no float64.
- **Fallback:** with the CPU-fallback box checked, Metal out-of-memory errors, Metal/MPS operator errors and non-finite outputs finish the job on CPU with a report warning; the GPU traceback stays in `worker.log`. Other errors (for example a model download failure) are raised, not retried.
- **Speed, end to end through the web API (model already cached):** a 3-second stereo clip took 85.4 s on CPU and 21.4 s on the Apple GPU (4.0×). A 30.1-second clip took 89.3 s on the Apple GPU; two real 30.1-second videos had taken 341.9 s and 477.2 s on CPU earlier the same day. Per 8-second inference window: about 1.7–1.9 s on Metal versus 6.4–12.5 s on CPU depending on load. More CPU threads (8 or 12 instead of 6) did not help.
- **Parity:** Apple GPU stems differ from CPU stems by about 70 dB below the mixture (maximum sample difference 0.0011 on the 3-second fixture); every output was finite with matching frames, sample rate and channels. This is numerical agreement, not a listening test.
- **Not enabled:** batching 4 windows per forward pass measured about 1.4× faster again on Metal but used about 6.4 GiB of GPU memory (10 windows: 16.8 GiB).
- **Tests:** 63 collected; 60 passed and 3 were skipped (mocked-OS launcher fixtures that only run on non-Mac hosts). New tests cover device selection, CPU retry after Metal failures, no retry for non-GPU errors or with fallback off, moving the model to Metal without float64 (run on real Metal), the API accepting `mps`, and `Cache-Control: no-cache` on the page. Ruff passed.
- **Page caching fix:** `/` now sends `Cache-Control: no-cache`. Without it, browsers kept a heuristically cached `index.html` and its old `app.js` version tag, so the Apple GPU option did not appear after the update.

## Mac package

- Added a Mac `.command` launcher, architecture-aware CPU installation, and a plain-text installation/troubleshooting guide. The Mac ZIP contains executable Unix permissions and LF line endings for the launcher, even when built on Windows.
- Dependency resolution passed against macOS 12 targets for Apple Silicon/Python 3.11, 3.12 and 3.13, and Intel/Python 3.10 and 3.11. Native binary wheels were required for Torch, NumPy, SciPy, SoundFile, FFmpeg, Pydantic Core, and CFFI. These checks resolve packages; they do not execute macOS binaries.
- Intel uses PyTorch 2.2.2, NumPy 1.26.4 and SciPy 1.14.1. Python 3.12 is explicitly rejected on Intel because PyTorch 2.2's compiler entry point rejects that Python version even with compilation disabled. Apple Silicon uses PyTorch 2.8.0.
- **55 automated tests passed** on the existing Windows/Python 3.12/PyTorch 2.8 environment and in an isolated Windows/Python 3.11/PyTorch 2.2.2 environment. The 17 added checks cover Mac dependency selection, unsupported configurations, preserved Windows/Linux device selection, ZIP contents/permissions, and first/repeat/repair launcher runs with mocked macOS/Python executables in a folder containing spaces. Bash syntax and Ruff checks passed.
- **Real inference with the Intel dependency versions:** Python 3.11.14, PyTorch 2.2.2+cpu, NumPy 1.26.4 and SciPy 1.14.1 completed the full pipeline on Windows using the official weights. A 0.25-second mono 32 kHz fixture took 145.08 seconds; all three exported stems had 8,000 finite samples at the original rate. This verifies the older Python/dependency combination, not Mac performance or macOS binary compatibility.
- macOS installation, Finder launch, Safari, and audio inference on actual Mac hardware have **not** been exercised. The Mac package currently uses CPU; Metal/MPS acceleration is not implemented.

## Interface refresh

The redesigned interface uses local SVG icons, an abstract animated signal graphic, cyan accents, and separate dialogue/music/effects colors. There are no external font or image requests.

Creator/community branding update: added "Made by cyr4x", the four supplied social destinations, and "Made for Higgsfield Community" with the site's navigation glyph and Space Grotesk wordmark typography. Desktop (1440 px) and mobile (390 px) screenshots were inspected; 320 px layout checks found no horizontal overflow or clipped social buttons. All four social destinations and new-tab protections were checked. The bundled logo and font loaded successfully with zero external resource requests; no JavaScript errors were reported. The Instagram link goes directly to the supplied profile instead of through YouTube's redirect. Both ZIPs include the same HTML, styles, logo, font and full font license.

- Visually inspected at desktop and phone sizes; horizontal overflow checks passed at 320, 390, 768 and 1440 pixels.
- Real file upload and GPU separation completed through the redesigned interface, followed by a preset change and successful remix without a second separation.
- Keyboard slider input updated the percentage, slider fill and REDUCED state together. KEEP and REMOVE states matched the selected presets.
- The reduced-motion preference disabled the signal animation. No JavaScript errors were reported; JavaScript syntax checking passed.

## Separation engine

Tested on Windows with Python 3.12.3, PyTorch 2.8.0+cu128 and an NVIDIA GeForce RTX 5090.

- **38 separation/API tests passed**, plus the 17 setup/package checks above. They cover mix routing, silent mixes, exact output length, mono/stereo, sample rates from 8–96 kHz, clipping bounds, corrupt inputs, AAC/video-container decoding, upload limits, cancellation routing, file access boundaries, GPU fallback, crossfading, and optimized-window parity with upstream padding/folding.
- Ruff lint checks passed.
- **Real GPU inference:** a 3-second 32 kHz stereo synthetic fixture completed in 9.95 seconds through the upload UI, using the official pretrained weights. Every exported track contained exactly 96,000 frames, two channels and 32 kHz audio.
- **Real CPU inference:** a 0.25-second 32 kHz mono synthetic fixture completed in 81.19 seconds. Every exported track retained 8,000 frames and one channel. CPU is substantially slower; these timings are specific to this machine and fixture, not benchmarks for longer clips.
- **Real-weight optimization comparison:** all three optimized stems matched an earlier upstream inference run exactly (maximum absolute sample error 0.0 for each stem on the 3-second fixture). The optimization needed 10 contributing window passes rather than 52 padded passes.
- The local HTTP download endpoints returned valid 24-bit WAV tracks, correctly routed cleaned mixes, and ZIPs with five intact entries. Remixing via the browser was verified without another inference run.
- Desktop (1440 px) and mobile (390 px) layouts were visually inspected. The mobile layout had no horizontal overflow. The cleaned mix loaded as playable audio with its expected duration; no JavaScript errors were reported.

This is functional and numerical verification. Synthetic fixtures do not establish audible separation quality on Seedance dialogue, music or effects. No subjective quality improvement is claimed. macOS/Linux installation has not been exercised here. The PyTorch 2.8 CPU run used the CUDA-capable build with tensors on CPU; its separate CPU-only wheel has not been exercised. The additional PyTorch 2.2.2 run above used the CPU-only Windows build.

Browser-automation downloads reported cancellation in the test browser, although the same output endpoints returned complete verified files over HTTP. Generated files are also retained in `data/<session-id>/output/` regardless of browser download behavior.
