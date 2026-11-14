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
