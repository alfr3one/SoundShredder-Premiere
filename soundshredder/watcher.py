"""Watch one folder; separate every new video into a multitrack .mov for editing.

The watcher runs as a thread inside the server. It only looks at the top level
of the folder, waits until a new file has stopped growing (so half-copied files
are never read), then queues it. One video is separated at a time, sharing the
server's single-job rule with uploads. State lives in ``watch.json`` so a
restart neither forgets the queue nor re-processes finished videos.
"""

from __future__ import annotations

import contextlib
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .audio import read_json, write_json
from .video import OUTPUT_SUFFIX, VIDEO_EXTENSIONS

POLL_SECONDS = 2.0
SETTLE_SECONDS = 4.0
MAX_ITEMS = 200
DEFAULT_OUTPUT = "SoundShredder Stems"
FINISHED = {"done", "failed", "cancelled"}


class Busy(Exception):
    """Another separation is running; try again on the next poll."""


def is_candidate(path: Path) -> bool:
    name = path.name
    return (
        path.suffix.lower() in VIDEO_EXTENSIONS
        and not name.startswith(".")  # includes macOS "._" files on external drives
        and not name.endswith(OUTPUT_SUFFIX)
    )


def copying(stat) -> bool:
    """Finder stamps a file's creation date as 24 Jan 1984 until it finishes copying it."""
    birth = getattr(stat, "st_birthtime", None)
    return birth is not None and datetime.fromtimestamp(birth).year == 1984


def scan(folder: Path) -> dict[str, tuple[int, int, bool]]:
    found = {}
    with contextlib.suppress(OSError):
        for entry in folder.iterdir():
            if not is_candidate(entry):
                continue
            with contextlib.suppress(OSError):
                stat = entry.stat()
                if entry.is_file():
                    found[entry.name] = (stat.st_size, stat.st_mtime_ns, copying(stat))
    return found


class FolderWatcher:
    def __init__(
        self,
        state_path: Path,
        start_job: Callable[[Path, dict], str],
        job_state: Callable[[str], dict | None],
        discard_job: Callable[[str], None] = lambda _job: None,
    ):
        self.state_path = state_path
        self.start_job = start_job
        self.job_state = job_state
        self.discard_job = discard_job
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.state = self.load()

    # Persistence

    def load(self) -> dict:
        state = {"folder": None, "output": None, "enabled": False, "device": "auto", "cpu_fallback": True}
        with contextlib.suppress(OSError, ValueError):
            state.update(read_json(self.state_path))
        state.setdefault("items", {})
        state.setdefault("baseline", [])
        # Separations are stopped when the server closes. Queue those videos again.
        for item in state["items"].values():
            if item["status"] == "running":
                if item.get("job"):
                    self.discard_job(item["job"])
                item.update(status="queued", job=None, message="Restarting after SoundShredder closed.")
        return state

    def save(self) -> None:
        items = self.state["items"]
        if len(items) > MAX_ITEMS:
            finished = sorted((k for k, v in items.items() if v["status"] in FINISHED), key=lambda k: items[k]["seen"])
            for key in finished[: len(items) - MAX_ITEMS]:
                del items[key]
        write_json(self.state_path, self.state)

    # Configuration

    def configure(
        self,
        folder: str,
        output: str | None,
        enabled: bool,
        device: str,
        cpu_fallback: bool,
        include_existing: bool = False,
    ) -> dict:
        watch = Path(folder).expanduser()
        if not watch.is_absolute() or not watch.is_dir():
            raise ValueError("Choose an existing folder to watch.")
        watch = watch.resolve()
        target = Path(output).expanduser() if output else watch / DEFAULT_OUTPUT
        if not target.is_absolute():
            raise ValueError("Choose a full folder path for the multitrack videos.")
        target = target.resolve()
        if target == watch:
            raise ValueError("Save the multitrack videos to a different folder than the one being watched.")
        if device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("Choose Auto, CPU, NVIDIA GPU, or Apple GPU.")
        if enabled:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"The output folder could not be created: {exc.strerror or exc}") from exc
        with self.lock:
            state = self.state
            if state["folder"] != str(watch):
                # A new folder: start a fresh list. Videos already there are left alone
                # unless asked; only videos added from now on are separated.
                state["items"] = {}
                state["baseline"] = [] if include_existing else sorted(scan(watch))
            elif include_existing:
                state["baseline"] = []
            state.update(
                folder=str(watch), output=str(target), enabled=enabled, device=device, cpu_fallback=cpu_fallback
            )
            self.save()
        self.wake()
        return self.snapshot()

    def set_enabled(self, enabled: bool) -> dict:
        with self.lock:
            if enabled and not self.state["folder"]:
                raise ValueError("Choose a folder to watch first.")
            self.state["enabled"] = enabled
            self.save()
        self.wake()
        return self.snapshot()

    def retry(self, name: str) -> dict:
        with self.lock:
            item = self.state["items"].get(name)
            if not item or item["status"] not in FINISHED:
                raise KeyError(name)
            item.update(status="waiting", job=None, message="", output=None, stable_since=None)
            self.save()
        self.wake()
        return self.snapshot()

    def snapshot(self) -> dict:
        with self.lock:
            state = self.state
            items = sorted(state["items"].values(), key=lambda item: item["seen"], reverse=True)
            folder = Path(state["folder"]) if state["folder"] else None
            return {
                "folder": state["folder"],
                "output": state["output"],
                "enabled": state["enabled"],
                "device": state["device"],
                "cpu_fallback": state["cpu_fallback"],
                "folder_found": bool(folder and folder.is_dir()),
                "skipped_existing": len(state["baseline"]),
                "items": [{key: value for key, value in item.items() if key != "stable_since"} for item in items],
            }

    # Polling

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name="soundshredder-watch", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10)

    def wake(self) -> None:
        self.wake_event.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc()  # keep watching; the error lands in the server log
            self.wake_event.wait(POLL_SECONDS)
            self.wake_event.clear()

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            state = self.state
            changed = self.follow_jobs()
            folder = Path(state["folder"]) if state["folder"] else None
            if state["enabled"] and folder and folder.is_dir():
                changed |= self.detect(folder, now)
                changed |= self.dispatch(folder)
            if changed:
                self.save()

    def follow_jobs(self) -> bool:
        changed = False
        for item in self.state["items"].values():
            if item["status"] != "running":
                continue
            job = self.job_state(item["job"])
            if job is None:
                item.update(status="cancelled", message="The session was deleted.")
            elif job["status"] == "running":
                if item.get("progress") != job.get("progress"):
                    item.update(progress=job.get("progress", 0), message=job.get("message", ""))
                    changed = True
                continue
            elif job["status"] == "complete":
                video = (job.get("report") or {}).get("video") or {}
                item.update(status="done", progress=1, output=video.get("file"), message=job.get("message", ""))
            else:
                item.update(status=job["status"] if job["status"] == "cancelled" else "failed")
                item.update(message=job.get("message", ""))
            item["finished"] = time.time()
            changed = True
        return changed

    def detect(self, folder: Path, now: float) -> bool:
        changed = False
        items = self.state["items"]
        baseline = set(self.state["baseline"])
        found = scan(folder)
        if baseline - found.keys():
            # A pre-existing video that is removed and added again counts as new.
            self.state["baseline"] = sorted(baseline & found.keys())
            changed = True
        for name, (size, mtime, busy) in found.items():
            if name in baseline:
                continue
            item = items.get(name)
            if item and item["status"] in FINISHED and (item["size"], item["mtime"]) != (size, mtime):
                item = None  # replaced by a new file with the same name: separate it again
            if item is None:
                items[name] = {
                    "name": name,
                    "path": str(folder / name),
                    "size": size,
                    "mtime": mtime,
                    "status": "waiting",
                    "message": "Waiting for the file to finish copying.",
                    "job": None,
                    "output": None,
                    "progress": 0,
                    "seen": now,
                    "stable_since": now,
                }
                changed = True
                continue
            if item["status"] != "waiting":
                continue
            if (item["size"], item["mtime"]) != (size, mtime) or busy or not size or item.get("stable_since") is None:
                item.update(size=size, mtime=mtime, stable_since=now)
                changed = True
            elif now - item["stable_since"] >= SETTLE_SECONDS:
                item.update(status="queued", message="Queued for separation.")
                changed = True
        for name, item in list(items.items()):
            if name not in found and item["status"] in {"waiting", "queued"}:
                del items[name]  # moved away or deleted before it was processed
                changed = True
        return changed

    def dispatch(self, folder: Path) -> bool:
        queued = [item for item in self.state["items"].values() if item["status"] == "queued"]
        if not queued or any(item["status"] == "running" for item in self.state["items"].values()):
            return False
        item = min(queued, key=lambda entry: entry["seen"])
        settings = {
            "device": self.state["device"],
            "cpu_fallback": self.state["cpu_fallback"],
            "export_dir": self.state["output"],
        }
        try:
            job = self.start_job(folder / item["name"], settings)
        except Busy:
            return False
        except Exception as exc:  # for example the file vanished or the output folder is gone
            item.update(status="failed", message=str(exc)[:400] or type(exc).__name__, finished=time.time())
            return True
        item.update(status="running", job=job, progress=0.01, message="Starting the separation engine…")
        return True
