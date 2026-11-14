import json
import threading
import urllib.error
import urllib.request

import pytest

from app.config import Config
from app.service import Service
from app.web import DashboardServer


@pytest.fixture()
def server(tmp_path, monkeypatch):
    cfg = Config(share_dir=tmp_path / "share", state_dir=tmp_path / "state")
    service = Service(cfg)
    monkeypatch.setattr(service, "worker_alive", lambda: True)
    httpd = DashboardServer(0, service)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, service
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def url(server, path):
    return f"http://127.0.0.1:{server.server_port}{path}"


def test_status_and_html(server):
    httpd, _ = server
    with urllib.request.urlopen(url(httpd, "/api/status")) as response:
        data = json.load(response)
        assert data["state"] == "starting"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    with urllib.request.urlopen(url(httpd, "/")) as response:
        assert b"DVD Bridge" in response.read()
    with urllib.request.urlopen(url(httpd, "/healthz")) as response:
        assert json.load(response)["ok"] is True


def test_command_requires_csrf_header(server):
    httpd, service = server
    request = urllib.request.Request(
        url(httpd, "/api/command"),
        data=json.dumps({"command": "retry"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 403
    request.add_header("X-DVD-Bridge", "1")
    with urllib.request.urlopen(request) as response:
        assert response.status == 202
    assert service.commands.get_nowait() == "retry"


def test_cross_origin_rejected(server):
    httpd, _ = server
    request = urllib.request.Request(
        url(httpd, "/api/command"),
        data=b'{"command":"eject"}',
        headers={
            "Content-Type": "application/json",
            "X-DVD-Bridge": "1",
            "Origin": "https://example.com",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 403


def test_unknown_command_rejected(server):
    httpd, _ = server
    request = urllib.request.Request(
        url(httpd, "/api/command"),
        data=b'{"command":"format"}',
        headers={"Content-Type": "application/json", "X-DVD-Bridge": "1"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400


def test_no_path_traversal(server):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(url(httpd, "/../app/config.py"))
    assert exc.value.code == 404


def post_json(httpd, path, payload):
    request = urllib.request.Request(
        url(httpd, path),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-DVD-Bridge": "1"},
        method="POST",
    )
    return urllib.request.urlopen(request)


def test_media_endpoint(server):
    httpd, _ = server
    with urllib.request.urlopen(url(httpd, "/api/media")) as response:
        data = json.load(response)
    assert data["sources"] == []
    assert set(data["destinations"]) == {"films", "music"}


def test_convert_endpoint_queues_job(server):
    httpd, service = server
    with post_json(
        httpd,
        "/api/convert",
        {"source_id": "abc", "title": 2, "mode": "audio", "destination": "music", "name": "Test"},
    ) as response:
        assert response.status == 202
    job = service.commands.get_nowait()
    assert job.source_id == "abc" and job.title == 2
    assert job.mode == "audio" and job.destination == "music" and job.name == "Test"


def test_convert_endpoint_defaults_by_mode(server):
    httpd, service = server
    with post_json(httpd, "/api/convert", {"source_id": "abc", "mode": "audio"}) as response:
        assert response.status == 202
    job = service.commands.get_nowait()
    assert job.title == 0 and job.destination == "music"


@pytest.mark.parametrize(
    "payload",
    [
        {"source_id": "abc", "title": "1"},
        {"source_id": "abc", "mode": "mkv"},
        {"source_id": "abc", "destination": "/etc"},
        {"source_id": "abc", "name": 123},
        {"source_id": "abc", "name": "x" * 201},
    ],
)
def test_convert_endpoint_rejects_invalid(server, payload):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/convert", payload)
    assert exc.value.code == 400


def test_convert_endpoint_requires_csrf(server):
    httpd, _ = server
    request = urllib.request.Request(
        url(httpd, "/api/convert"),
        data=b'{"source_id":"abc"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 403


def test_close_and_cancel_commands(server):
    httpd, service = server
    with post_json(httpd, "/api/command", {"command": "close"}) as response:
        assert response.status == 202
    assert service.commands.get_nowait() == "close"
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "cancel"})
    assert exc.value.code == 409


def test_image_and_copy_cancel_commands(server):
    httpd, service = server
    for command in ("cancel_iso", "cancel_copy"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            post_json(httpd, "/api/command", {"command": command})
        assert exc.value.code == 409
    service.update(image_state="working")
    with post_json(httpd, "/api/command", {"command": "cancel_iso"}) as response:
        assert response.status == 202
    assert service._image_stop.is_set()
    service.update(copy_state="copying")
    with post_json(httpd, "/api/command", {"command": "cancel_copy"}) as response:
        assert response.status == 202
    assert service._copy_stop.is_set()


def test_iso_force_requires_damage_prompt(server):
    httpd, service = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "iso_force"})
    assert exc.value.code == 409
    service.update(image_state="error", image_force_available=True)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "iso_force"})
    assert exc.value.code == 409  # no mounted disc in this fixture
