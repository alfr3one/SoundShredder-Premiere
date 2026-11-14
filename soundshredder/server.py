"""Loopback-only UI and job API. Audio stays on this computer."""

from __future__ import annotations

import contextlib
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audio import EXTENSIONS, MAX_BYTES, export_mix, read_json, validate_gains, write_json
from .watcher import Busy, FolderWatcher

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SOUNDSHREDDER_DATA", str(ROOT / "data"))).resolve()
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
PROCESSES: dict[str, subprocess.Popen] = {}
PENDING: list[str] = []  # queued job ids, oldest first; started one at a time
GUARD = threading.RLock()
MIX_LOCK = threading.Lock()


def job_path(job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id) or not (DATA / job_id / "request.json").is_file():
        raise HTTPException(404, "That session was not found.")
    return DATA / job_id


def active_jobs() -> list[str]:
    return [job for job, process in PROCESSES.items() if process.poll() is None]


def snapshot(job_id: str) -> dict:
    directory = job_path(job_id)
    progress = read_json(directory / "progress.json")
    process = PROCESSES.get(job_id)
    if progress["status"] == "running" and (process is None or process.poll() is not None):
        progress = {
            "status": "failed",
            "progress": 0,
            "message": "Processing stopped before completion. Start a new separation to retry.",
        }
    settings = read_json(directory / "request.json")
    result = {
        "id": job_id,
        **progress,
        "filename": settings["filename"],
        "origin": settings.get("origin") or ("watch" if settings.get("source_path") else "upload"),
        "settings": {key: settings[key] for key in ("device", "gains", "cpu_fallback") if key in settings},
    }
    for key, filename in (("source", "source.json"), ("report", "separation.json"), ("mix", "mix.json")):
        if (directory / filename).is_file():
            result[key] = read_json(directory / filename)
    return result


def start_worker(job_id: str, directory: Path) -> None:
    """Start one separation process. Call with GUARD held after checking active_jobs()."""
    write_json(
        directory / "progress.json",
        {"status": "running", "progress": 0.01, "message": "Starting the separation engine…"},
    )
    with (directory / "worker.log").open("wb") as log:
        PROCESSES[job_id] = subprocess.Popen(
            [sys.executable, "-m", "soundshredder.worker", str(directory)],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def start_watch_job(source: Path, settings: dict) -> str:
    """Separate a video from the watched folder in place; the worker writes the .mov to export_dir."""
    if not source.is_file():
        raise ValueError("The video is no longer in the watched folder.")
    with GUARD:
        if active_jobs() or PENDING:  # clips requested from Premiere go first
            raise Busy
        job_id = uuid.uuid4().hex
        directory = DATA / job_id
        directory.mkdir(parents=True)
        try:
            write_json(
                directory / "request.json",
                {
                    "filename": source.name,
                    "source": source.name,
                    "source_path": str(source),
                    "device": settings["device"],
                    "gains": {"speech": 1, "music": 0, "effects": 1},
                    "cpu_fallback": settings["cpu_fallback"],
                    "export_dir": settings["export_dir"],
                    "origin": "watch",
                },
            )
            start_worker(job_id, directory)
        except BaseException:
            if job_id not in PROCESSES:
                shutil.rmtree(directory, ignore_errors=True)
            raise
    return job_id


def watch_job_state(job_id: str) -> dict | None:
    try:
        return snapshot(job_id)
    except (HTTPException, OSError, ValueError):
        return None


def discard_job(job_id: str) -> None:
    """Remove an interrupted watched-folder session; its video is separated again from scratch."""
    directory = DATA / job_id
    if JOB_ID.fullmatch(job_id) and directory.resolve().parent == DATA and job_id not in active_jobs():
        shutil.rmtree(directory, ignore_errors=True)


def queue_job(job_id: str, directory: Path) -> None:
    """Hold a job until nothing else is separating. Call with GUARD held."""
    write_json(
        directory / "progress.json",
        {"status": "queued", "progress": 0, "message": "Waiting for the current separation to finish."},
    )
    PENDING.append(job_id)


def start_next_queued() -> bool:
    with GUARD:
        if active_jobs():
            return False
        while PENDING:
            job_id = PENDING.pop(0)
            directory = DATA / job_id
            try:
                if read_json(directory / "progress.json")["status"] != "queued":
                    continue
            except (OSError, ValueError, KeyError):
                continue
            start_worker(job_id, directory)
            return True
    return False


def requeue_saved_jobs() -> None:
    """Jobs still queued when SoundShredder closed wait in the same order after a restart."""
    queued = []
    for directory in DATA.iterdir():
        if JOB_ID.fullmatch(directory.name):
            with contextlib.suppress(OSError, ValueError, KeyError):
                if read_json(directory / "progress.json")["status"] == "queued":
                    queued.append(directory)
    PENDING[:] = [directory.name for directory in sorted(queued, key=lambda d: (d / "request.json").stat().st_mtime)]


def dispatch_loop(stop: threading.Event) -> None:
    while not stop.wait(1):
        try:
            start_next_queued()
        except Exception:
            traceback.print_exc()


WATCHER: FolderWatcher | None = None


def watcher() -> FolderWatcher:
    if WATCHER is None:
        raise HTTPException(503, "SoundShredder is still starting. Try again in a moment.")
    return WATCHER


@asynccontextmanager
async def lifespan(_app):
    global WATCHER
    DATA.mkdir(parents=True, exist_ok=True)
    requeue_saved_jobs()
    stop = threading.Event()
    dispatcher = threading.Thread(target=dispatch_loop, args=(stop,), name="soundshredder-queue", daemon=True)
    dispatcher.start()
    WATCHER = FolderWatcher(DATA / "watch.json", start_watch_job, watch_job_state, discard_job)
    WATCHER.start()
    yield
    stop.set()
    WATCHER.stop()
    with GUARD:
        for job_id in active_jobs():
            process = PROCESSES[job_id]
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            write_json(
                DATA / job_id / "progress.json",
                {"status": "cancelled", "progress": 0, "message": "Stopped when SoundShredder closed."},
            )


app = FastAPI(title="SoundShredder", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_only(request: Request, call_next):
    # No public sharing, wildcard CORS, or cross-origin mutations of local files.
    if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Open SoundShredder on this computer to make changes."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    # Revalidate every load (cheap ETag 304s). Without this, browsers cache the page
    # heuristically for days and keep loading an outdated app.js version tag.
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/system")
def system():
    import torch

    available = torch.cuda.is_available()
    return {
        "name": "SoundShredder",
        "gpu_available": available,
        "gpu_name": torch.cuda.get_device_name(0) if available else None,
        # Same rule as engine.apple_gpu_available (the engine stays out of this process).
        "mps_available": platform.machine() == "arm64" and torch.backends.mps.is_available(),
        "torch": torch.__version__,
        "max_mb": MAX_BYTES // (1024 * 1024),
        "active_jobs": active_jobs(),
        "queued_jobs": list(PENDING),
        "features": ["watch", "clips"],
    }


@app.get("/api/jobs")
def history():
    directories = sorted(
        (p for p in DATA.iterdir() if p.is_dir() and JOB_ID.fullmatch(p.name)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:20]
    jobs = []
    for directory in directories:
        with contextlib.suppress(OSError, ValueError, HTTPException):
            jobs.append(snapshot(directory.name))
    return jobs


@app.post("/api/jobs", status_code=202)
async def create_job(
    file: Annotated[UploadFile, File()],
    device: Annotated[str, Form()] = "auto",
    speech: Annotated[float, Form()] = 1,
    music: Annotated[float, Form()] = 0,
    effects: Annotated[float, Form()] = 1,
    cpu_fallback: Annotated[bool, Form()] = True,
):
    if device not in {"auto", "cpu", "cuda", "mps"}:
        raise HTTPException(400, "Choose Auto, CPU, NVIDIA GPU, or Apple GPU.")
    try:
        gains = validate_gains({"speech": speech, "music": music, "effects": effects})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = (file.filename or "audio").replace("\\", "/").split("/")[-1][:200]
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS:
        raise HTTPException(400, "Upload WAV, MP3, FLAC, M4A, AAC, OGG, AIFF, or a video with audio.")
    with GUARD:
        if active_jobs():
            origin = read_json(DATA / active_jobs()[0] / "request.json").get("origin")
            raise HTTPException(
                409,
                {
                    "watch": "A video from the watched folder is being separated. Try again when it finishes.",
                    "premiere": "A clip from Premiere is being separated. Try again when it finishes.",
                }.get(origin, "A separation is already running. Wait for it to finish or cancel it first."),
            )
    job_id = uuid.uuid4().hex
    directory = DATA / job_id
    directory.mkdir(parents=True)
    source_name = f"source{extension}"
    size = 0
    try:
        with (directory / source_name).open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "The file is too large. The limit is 500 MB.")
                destination.write(chunk)
        if not size:
            raise HTTPException(400, "The uploaded file is empty.")
        write_json(
            directory / "request.json",
            {
                "filename": filename,
                "source": source_name,
                "device": device,
                "gains": gains,
                "cpu_fallback": cpu_fallback,
            },
        )
        with GUARD:
            if active_jobs():
                raise HTTPException(409, "Another separation just started. Please try again when it finishes.")
            start_worker(job_id, directory)
        return {"id": job_id}
    except BaseException:
        # This path is always a freshly generated UUID under DATA, never a user path.
        if job_id not in PROCESSES:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return snapshot(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    directory = job_path(job_id)
    with GUARD:
        process = PROCESSES.get(job_id)
        if job_id in PENDING:
            PENDING.remove(job_id)
            write_json(
                directory / "progress.json",
                {"status": "cancelled", "progress": 0, "message": "Removed from the queue."},
            )
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            write_json(
                directory / "progress.json",
                {
                    "status": "cancelled",
                    "progress": 0,
                    "message": "Separation cancelled. Your original file is unchanged.",
                },
            )
    return snapshot(job_id)


class MixLevels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speech: float = Field(ge=0, le=1, allow_inf_nan=False)
    music: float = Field(ge=0, le=1, allow_inf_nan=False)
    effects: float = Field(ge=0, le=1, allow_inf_nan=False)


@app.post("/api/jobs/{job_id}/mix")
def remix(job_id: str, levels: MixLevels):
    directory = job_path(job_id)
    if snapshot(job_id)["status"] != "complete":
        raise HTTPException(409, "Wait until separation is complete to adjust the mix.")
    with MIX_LOCK:
        return export_mix(directory, levels.model_dump())


@app.get("/api/jobs/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    directory = job_path(job_id)
    allowed = re.fullmatch(
        r"(?:speech|music|effects|original)\.wav|cleaned-[a-f0-9]{12}\.wav|soundshredder-[a-f0-9]{12}\.zip|report-[a-f0-9]{12}\.json",
        filename,
    )
    if not allowed or not (directory / "output" / filename).is_file():
        raise HTTPException(404, "That output file is not available.")
    return FileResponse(directory / "output" / filename, filename=filename)


def multitrack_file(job_id: str) -> Path:
    directory = job_path(job_id)
    report = directory / "separation.json"
    video = read_json(report).get("video") if report.is_file() else None
    path = Path(video["file"]) if video else None
    if path is None or not path.is_file():
        raise HTTPException(404, "The multitrack video is not available. It may have been moved or deleted.")
    return path


@app.get("/api/jobs/{job_id}/video")
def download_video(job_id: str):
    path = multitrack_file(job_id)
    return FileResponse(path, filename=path.name, media_type="video/quicktime")


def reveal(path: Path) -> None:
    """Select a file in Finder / Explorer, or open its folder elsewhere."""
    if sys.platform == "darwin":
        command = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
    elif sys.platform == "win32":
        command = ["explorer", f"/select,{path}"] if path.is_file() else ["explorer", str(path)]
    else:
        command = ["xdg-open", str(path if path.is_dir() else path.parent)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@app.post("/api/jobs/{job_id}/reveal")
def reveal_video(job_id: str):
    reveal(multitrack_file(job_id))
    return {"revealed": True}


class WatchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(min_length=1, max_length=4096)
    output: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    device: str = "auto"
    cpu_fallback: bool = True
    include_existing: bool = False


class WatchToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@app.get("/api/watch")
def watch_status():
    return {**watcher().snapshot(), "can_pick": sys.platform == "darwin"}


@app.post("/api/watch")
def watch_configure(settings: WatchSettings):
    try:
        state = watcher().configure(
            settings.folder.strip(),
            (settings.output or "").strip() or None,
            settings.enabled,
            settings.device,
            settings.cpu_fallback,
            settings.include_existing,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**state, "can_pick": sys.platform == "darwin"}


@app.post("/api/watch/enabled")
def watch_toggle(toggle: WatchToggle):
    try:
        state = watcher().set_enabled(toggle.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**state, "can_pick": sys.platform == "darwin"}


@app.post("/api/watch/retry")
def watch_retry(item: dict):
    try:
        state = watcher().retry(str(item.get("name", "")))
    except KeyError as exc:
        raise HTTPException(404, "That video is not waiting for a retry.") from exc
    return {**state, "can_pick": sys.platform == "darwin"}


@app.post("/api/watch/reveal")
def watch_reveal(target: dict):
    """Show the watched or output folder in Finder."""
    state = watcher().snapshot()
    key = target.get("folder")
    if key not in {"folder", "output"} or not state[key]:
        raise HTTPException(404, "That folder is not set.")
    path = Path(state[key])
    if not path.is_dir():
        raise HTTPException(404, "That folder does not exist yet.")
    reveal(path)
    return {"revealed": True}


PICK_SCRIPT = """on run argv
    tell current application
        activate
        set chosen to choose folder with prompt (item 1 of argv)
    end tell
    return POSIX path of chosen
end run"""


@app.post("/api/watch/pick")
def watch_pick(target: dict):
    """Show the macOS folder picker. The browser cannot reveal a folder's real path."""
    if sys.platform != "darwin":
        raise HTTPException(501, "Type or paste the folder path instead.")
    prompt = "Choose the folder to watch for new videos"
    if target.get("folder") == "output":
        prompt = "Choose where to save the multitrack videos"
    try:
        result = subprocess.run(
            ["osascript", "-e", PICK_SCRIPT, prompt], capture_output=True, text=True, timeout=600, check=False
        )
    except subprocess.TimeoutExpired:
        return {"path": None}
    path = result.stdout.strip()
    if result.returncode or not path:
        return {"path": None}  # cancelled
    return {"path": path.rstrip("/") or "/"}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    directory = job_path(job_id)
    with GUARD, MIX_LOCK:
        if job_id in active_jobs():
            raise HTTPException(409, "Cancel the separation before deleting this session.")
        # Resolve and verify the target before any recursive removal.
        if directory.resolve().parent != DATA or not JOB_ID.fullmatch(directory.name):
            raise HTTPException(400, "Invalid session directory.")
        shutil.rmtree(directory)
        PROCESSES.pop(job_id, None)
        if job_id in PENDING:
            PENDING.remove(job_id)
    return {"deleted": True}


class ClipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    device: str = "auto"
    cpu_fallback: bool = True


def reusable_clip_job(source: Path, signature: list[int]) -> str | None:
    """A finished (or still running) separation of this exact file, so repeated requests are instant."""
    directories = sorted(
        (p for p in DATA.iterdir() if JOB_ID.fullmatch(p.name)), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for directory in directories:
        with contextlib.suppress(OSError, ValueError, KeyError, HTTPException):
            request = read_json(directory / "request.json")
            if request.get("origin") != "premiere" or request.get("source_path") != str(source):
                continue
            if request.get("source_signature") != signature:
                continue
            job = snapshot(directory.name)
            if job["status"] in {"queued", "running"}:
                return directory.name
            files = ((job.get("report") or {}).get("stems_export") or {}).get("files", {})
            if job["status"] == "complete" and files and all(Path(path).is_file() for path in files.values()):
                return directory.name
    return None


@app.post("/api/clips", status_code=202)
def separate_clip(clip: ClipRequest):
    """Separate a media file used in an editor's timeline, in place; stems are saved next to it."""
    if clip.device not in {"auto", "cpu", "cuda", "mps"}:
        raise HTTPException(400, "Choose Auto, CPU, NVIDIA GPU, or Apple GPU.")
    source = Path(clip.path).expanduser()
    if not source.is_absolute() or not source.is_file():
        raise HTTPException(400, f"The clip's media file was not found: {clip.path}")
    if source.suffix.lower() not in EXTENSIONS:
        raise HTTPException(400, f"{source.name} is not a supported audio or video file.")
    stat = source.stat()
    signature = [stat.st_size, stat.st_mtime_ns]
    with GUARD:
        existing = reusable_clip_job(source, signature)
        if existing:
            return {"id": existing, "reused": True}
        job_id = uuid.uuid4().hex
        directory = DATA / job_id
        directory.mkdir(parents=True)
        export = source.parent / "SoundShredder Stems"
        try:
            export.mkdir(exist_ok=True)
            writable = os.access(export, os.W_OK)
        except OSError:
            writable = False
        if not writable:  # read-only media: keep the stems in the session instead
            export = directory / "output"
        write_json(
            directory / "request.json",
            {
                "filename": source.name,
                "source": source.name,
                "source_path": str(source),
                "source_signature": signature,
                "device": clip.device,
                "gains": {"speech": 1, "music": 0, "effects": 1},
                "cpu_fallback": clip.cpu_fallback,
                "export_dir": str(export),
                "export_video": False,
                "export_stems": True,
                "origin": "premiere",
            },
        )
        queue_job(job_id, directory)
    start_next_queued()
    return {"id": job_id, "reused": False}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
