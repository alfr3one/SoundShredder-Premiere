import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from soundshredder import server


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -1

    def wait(self, timeout):
        return self.code

    def kill(self):
        self.code = -9


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "PROCESSES", {})
    monkeypatch.setattr(server.subprocess, "Popen", FakeProcess)
    with TestClient(server.app) as instance:
        yield instance


def wav():
    stream = io.BytesIO()
    sf.write(stream, np.zeros((80, 1)), 8000, format="WAV")
    return stream.getvalue()


def test_upload_cancel_and_delete(client):
    response = client.post("/api/jobs", files={"file": ("../../clip.wav", wav())})
    assert response.status_code == 202
    job = response.json()["id"]
    status = client.get(f"/api/jobs/{job}").json()
    assert status["filename"] == "clip.wav" and status["status"] == "running"
    assert client.post("/api/jobs", files={"file": ("clip.wav", wav())}).status_code == 409
    assert client.delete(f"/api/jobs/{job}").status_code == 409
    assert client.post(f"/api/jobs/{job}/cancel").json()["status"] == "cancelled"
    assert client.delete(f"/api/jobs/{job}").status_code == 200
    assert client.get(f"/api/jobs/{job}").status_code == 404


def test_apple_gpu_device_is_accepted_and_unknown_devices_are_not(client):
    assert client.post("/api/jobs", files={"file": ("clip.wav", wav())}, data={"device": "metal"}).status_code == 400
    response = client.post("/api/jobs", files={"file": ("clip.wav", wav())}, data={"device": "mps"})
    assert response.status_code == 202
    assert client.get(f"/api/jobs/{response.json()['id']}").json()["settings"]["device"] == "mps"
    assert "mps_available" in client.get("/api/system").json()
    page = client.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert 'value="mps"' in page.text


def test_invalid_upload_and_levels(client, monkeypatch):
    assert client.post("/api/jobs", files={"file": ("script.py", b"anything")}).status_code == 400
    assert client.post("/api/jobs", files={"file": ("clip.wav", b"")}).status_code == 400
    assert client.post("/api/jobs", files={"file": ("clip.wav", wav())}, data={"speech": "nan"}).status_code == 400
    monkeypatch.setattr(server, "MAX_BYTES", 4)
    assert client.post("/api/jobs", files={"file": ("clip.wav", wav())}).status_code == 413
    assert client.get("/api/jobs").json() == []


def test_local_origin_and_download_paths(client):
    assert (
        client.post(
            "/api/jobs", headers={"Origin": "https://external.example"}, files={"file": ("clip.wav", wav())}
        ).status_code
        == 403
    )
    job = client.post("/api/jobs", files={"file": ("clip.wav", wav())}).json()["id"]
    assert client.get(f"/api/jobs/{job}/files/source.wav").status_code == 404
    assert client.get(f"/api/jobs/{job}/files/worker.log").status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 404
    assert client.post(f"/api/jobs/{job}/mix", json={"speech": 1, "music": 0, "effects": 0}).status_code == 409
    assert client.post(f"/api/jobs/{job}/mix", json={"speech": 9, "music": 0, "effects": 0}).status_code == 422


def test_watch_folder_settings_and_watched_jobs(client, tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    assert client.get("/api/watch").json()["enabled"] is False
    assert client.post("/api/watch", json={"folder": str(tmp_path / "nope")}).status_code == 400
    state = client.post("/api/watch", json={"folder": str(folder), "device": "cpu"}).json()
    assert state["enabled"] and state["output"].endswith("SoundShredder Stems")
    assert client.post("/api/watch/enabled", json={"enabled": False}).json()["enabled"] is False
    assert client.post("/api/watch/retry", json={"name": "missing.mp4"}).status_code == 404
    (folder / "scene.mp4").write_bytes(b"video")
    job = server.start_watch_job(folder / "scene.mp4", {"device": "cpu", "cpu_fallback": True, "export_dir": "x"})
    status = client.get(f"/api/jobs/{job}").json()
    assert status["origin"] == "watch" and status["filename"] == "scene.mp4"
    with pytest.raises(server.Busy):
        server.start_watch_job(folder / "scene.mp4", {"device": "cpu", "cpu_fallback": True, "export_dir": "x"})
    response = client.post("/api/jobs", files={"file": ("clip.wav", wav())})
    assert response.status_code == 409 and "watched folder" in response.json()["detail"]
    assert client.get(f"/api/jobs/{job}/video").status_code == 404


def test_clips_from_premiere_queue_reuse_and_cancel(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PENDING", [])
    footage = tmp_path / "footage"
    footage.mkdir()
    for name in ("a.mp4", "b.mp4"):
        (footage / name).write_bytes(b"video " + name.encode())
    assert client.post("/api/clips", json={"path": "relative.mp4"}).status_code == 400
    assert client.post("/api/clips", json={"path": str(footage / "missing.mp4")}).status_code == 400
    first = client.post("/api/clips", json={"path": str(footage / "a.mp4"), "device": "mps"}).json()
    assert first["reused"] is False
    job = client.get(f"/api/jobs/{first['id']}").json()
    assert job["status"] == "running" and job["origin"] == "premiere"
    request = server.read_json(server.DATA / first["id"] / "request.json")
    assert request["export_dir"] == str(footage / "SoundShredder Stems") and request["export_stems"]
    assert request["export_video"] is False and request["source_path"] == str(footage / "a.mp4")
    # The same file again joins the existing job; another file waits in the queue.
    assert client.post("/api/clips", json={"path": str(footage / "a.mp4")}).json() == {**first, "reused": True}
    second = client.post("/api/clips", json={"path": str(footage / "b.mp4")}).json()
    assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "queued"
    assert client.get("/api/system").json()["queued_jobs"] == [second["id"]]
    # Uploads are refused with a reason; the watcher yields to Premiere clips.
    assert "Premiere" in client.post("/api/jobs", files={"file": ("c.wav", wav())}).json()["detail"]
    server.PROCESSES[first["id"]].code = 0
    with pytest.raises(server.Busy):
        server.start_watch_job(footage / "a.mp4", {"device": "cpu", "cpu_fallback": True, "export_dir": "x"})
    assert server.start_next_queued() is True
    assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "running"
    third = client.post("/api/clips", json={"path": str(footage / "c.mp4")})
    assert third.status_code == 400
    (footage / "c.mp4").write_bytes(b"c")
    third = client.post("/api/clips", json={"path": str(footage / "c.mp4")}).json()
    assert client.post(f"/api/jobs/{third['id']}/cancel").json()["status"] == "cancelled"
    assert server.PENDING == []


@pytest.mark.parametrize(
    ("system", "stdout", "expected", "program"),
    [
        ("darwin", "/Volumes/Drive/مشاهد/\n", "/Volumes/Drive/مشاهد", "osascript"),
        ("win32", "D:\\مشاهد\\Exports\\\r\n", "D:\\مشاهد\\Exports", "powershell"),
        ("win32", "E:\\\r\n", "E:\\", "powershell"),
    ],
)
def test_folder_picker_on_mac_and_windows(client, monkeypatch, system, stdout, expected, program):
    calls = []

    def fake_run(command, **options):
        calls.append((command, options))
        return type("Result", (), {"returncode": 0, "stdout": stdout})()

    monkeypatch.setattr(server.sys, "platform", system)
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    assert client.get("/api/watch").json()["can_pick"] is True
    assert client.post("/api/watch/pick", json={"folder": "output"}).json() == {"path": expected}
    command, options = calls[0]
    assert command[0] == program and options["encoding"] == "utf-8"
    if system == "win32":
        # The prompt travels in the environment, never inside the PowerShell code.
        assert options["env"]["SOUNDSHREDDER_PROMPT"] == "Choose where to save the multitrack videos"
        assert "SOUNDSHREDDER_PROMPT" not in command[-1].replace("$env:SOUNDSHREDDER_PROMPT", "")


def test_folder_picker_cancelled_or_unavailable(client, monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(server.subprocess, "run", lambda command, **_: type("R", (), {"returncode": 0, "stdout": ""})())
    assert client.post("/api/watch/pick", json={"folder": "folder"}).json() == {"path": None}
    monkeypatch.setattr(server.sys, "platform", "linux")
    assert client.get("/api/watch").json()["can_pick"] is False
    assert client.post("/api/watch/pick", json={"folder": "folder"}).status_code == 501
