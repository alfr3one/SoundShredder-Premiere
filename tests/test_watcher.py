import os

import pytest

from soundshredder import watcher as watch
from soundshredder.audio import read_json


class Engine:
    """Stands in for the server: one job at a time, statuses set by the test."""

    def __init__(self):
        self.jobs = {}
        self.started = []
        self.discarded = []

    def start(self, path, settings):
        if any(job["status"] == "running" for job in self.jobs.values()):
            raise watch.Busy
        job = f"job{len(self.started)}"
        self.started.append((path.name, settings))
        self.jobs[job] = {"status": "running", "progress": 0.1, "message": "Separating"}
        return job

    def state(self, job):
        return self.jobs.get(job)

    def finish(self, job, name):
        self.jobs[job] = {
            "status": "complete",
            "message": "Multitrack video saved",
            "report": {"video": {"file": f"/out/{name} - Stems.mov"}},
        }


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    engine = Engine()
    instance = watch.FolderWatcher(tmp_path / "watch.json", engine.start, engine.state, engine.discarded.append)
    return folder, engine, instance


def statuses(instance):
    return {item["name"]: item["status"] for item in instance.snapshot()["items"]}


def test_only_new_settled_videos_are_separated_one_at_a_time(setup):
    folder, engine, instance = setup
    (folder / "old.mp4").write_bytes(b"x" * 10)
    state = instance.configure(str(folder), None, True, "mps", True)
    assert state["output"] == str(folder.resolve() / watch.DEFAULT_OUTPUT)
    assert (folder / watch.DEFAULT_OUTPUT).is_dir() and state["skipped_existing"] == 1
    for name in ("a.mp4", "b.MOV", "notes.txt", ".hidden.mp4", "._a.mp4", "a - Stems.mov"):
        (folder / name).write_bytes(b"x" * 10)
    instance.tick(now=100)
    assert statuses(instance) == {"a.mp4": "waiting", "b.MOV": "waiting"}
    instance.tick(now=100 + watch.SETTLE_SECONDS)
    assert engine.started[0][0] == "a.mp4"
    assert engine.started[0][1] == {
        "device": "mps",
        "cpu_fallback": True,
        "export_dir": str(folder.resolve() / watch.DEFAULT_OUTPUT),
    }
    assert statuses(instance) == {"a.mp4": "running", "b.MOV": "queued"}
    instance.tick(now=110)
    assert len(engine.started) == 1  # the second video waits for the first
    engine.finish("job0", "a")
    instance.tick(now=111)
    assert statuses(instance)["a.mp4"] == "done" and engine.started[1][0] == "b.MOV"
    done = next(item for item in instance.snapshot()["items"] if item["name"] == "a.mp4")
    assert done["output"] == "/out/a - Stems.mov"
    instance.tick(now=200)
    assert len(engine.started) == 2  # finished videos are not separated again


def test_a_growing_file_waits_until_copying_ends(setup):
    folder, engine, instance = setup
    instance.configure(str(folder), None, True, "auto", True)
    clip = folder / "copying.mp4"
    clip.write_bytes(b"x" * 10)
    instance.tick(now=0)
    with clip.open("ab") as stream:
        stream.write(b"more")
    os.utime(clip, ns=(1, 2))
    instance.tick(now=watch.SETTLE_SECONDS)
    assert statuses(instance) == {"copying.mp4": "waiting"} and not engine.started
    instance.tick(now=2 * watch.SETTLE_SECONDS)
    assert engine.started == [("copying.mp4", engine.started[0][1])]


def test_paused_watcher_does_nothing_and_resumes_with_new_videos(setup):
    folder, engine, instance = setup
    instance.configure(str(folder), None, False, "auto", True)
    (folder / "added.mp4").write_bytes(b"x")
    instance.tick(now=0)
    instance.tick(now=60)
    assert not engine.started and not statuses(instance)
    instance.set_enabled(True)
    instance.tick(now=61)
    instance.tick(now=61 + watch.SETTLE_SECONDS)
    assert [name for name, _ in engine.started] == ["added.mp4"]


def test_include_existing_and_retry(setup):
    folder, engine, instance = setup
    (folder / "old.mp4").write_bytes(b"x")
    instance.configure(str(folder), None, True, "auto", True, include_existing=True)
    instance.tick(now=0)
    instance.tick(now=watch.SETTLE_SECONDS)
    engine.jobs["job0"] = {"status": "failed", "message": "This file could not be read."}
    instance.tick(now=10)
    item = instance.snapshot()["items"][0]
    assert item["status"] == "failed" and item["message"] == "This file could not be read."
    instance.retry("old.mp4")
    instance.tick(now=20)
    instance.tick(now=20 + watch.SETTLE_SECONDS)
    assert len(engine.started) == 2
    with pytest.raises(KeyError):
        instance.retry("old.mp4")  # running, not failed


def test_restart_requeues_interrupted_videos(setup, tmp_path):
    folder, engine, instance = setup
    instance.configure(str(folder), None, True, "auto", True)
    (folder / "a.mp4").write_bytes(b"x")
    instance.tick(now=0)
    instance.tick(now=watch.SETTLE_SECONDS)
    assert read_json(tmp_path / "watch.json")["items"]["a.mp4"]["status"] == "running"
    later = Engine()
    restarted = watch.FolderWatcher(tmp_path / "watch.json", later.start, later.state, later.discarded.append)
    assert later.discarded == ["job0"] and statuses(restarted) == {"a.mp4": "queued"}
    restarted.tick(now=50)
    assert [name for name, _ in later.started] == ["a.mp4"]


def test_invalid_settings_are_rejected(setup, tmp_path):
    folder, _, instance = setup
    with pytest.raises(ValueError, match="existing folder"):
        instance.configure(str(tmp_path / "missing"), None, True, "auto", True)
    with pytest.raises(ValueError, match="existing folder"):
        instance.configure("relative/path", None, True, "auto", True)
    with pytest.raises(ValueError, match="different folder"):
        instance.configure(str(folder), str(folder), True, "auto", True)
    with pytest.raises(ValueError, match="Choose a folder"):
        instance.set_enabled(True)
