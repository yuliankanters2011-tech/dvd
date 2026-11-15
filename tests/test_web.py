import json
import re
import threading
import urllib.error
import urllib.request
from html.parser import HTMLParser

import pytest

from app.config import Config
from app.service import Service
from app.web import WEB_ROOT, DashboardServer


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
    assert data["skipped_sources"] == []
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


def test_convert_endpoint_accepts_all_titles(server):
    httpd, service = server
    with post_json(httpd, "/api/convert", {"source_id": "abc", "title": -1}) as response:
        assert response.status == 202
    assert service.commands.get_nowait().title == -1


@pytest.mark.parametrize("title", [-2, 100, True, "1", 1.0])
def test_convert_endpoint_rejects_invalid_title_numbers(server, title):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/convert", {"source_id": "abc", "title": title})
    assert exc.value.code == 400


def test_sleep_and_wake_commands(server):
    httpd, service = server
    with post_json(httpd, "/api/command", {"command": "sleep"}) as response:
        assert response.status == 202
    assert service.commands.get_nowait() == "sleep"
    service._sleeping = True
    service.update(drive_sleeping=True, state="sleeping")
    with post_json(httpd, "/api/command", {"command": "wake"}) as response:
        assert response.status == 202
    assert service.commands.get_nowait() == "wake"


def test_speed_command_queues_numeric_job(server):
    httpd, service = server
    with post_json(httpd, "/api/command", {"command": "speed", "speed": 8}) as response:
        assert response.status == 202
        assert json.load(response)["speed"] == 8
    job = service.commands.get_nowait()
    assert job.speed == 8


@pytest.mark.parametrize("speed", [-1, 25, True, "8", 1.5, None])
def test_speed_command_rejects_invalid_numbers(server, speed):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "speed", "speed": speed})
    assert exc.value.code == 400


def test_speed_command_requires_number(server):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "speed"})
    assert exc.value.code == 400


def test_sleep_idle_command_updates_snapshot(server):
    httpd, service = server
    with post_json(httpd, "/api/command", {"command": "sleep_idle", "seconds": 300}) as response:
        assert response.status == 202
    assert service.snapshot()["sleep_idle_seconds"] == 300
    with post_json(httpd, "/api/command", {"command": "sleep_idle", "seconds": 0}) as response:
        assert response.status == 202
    assert service.snapshot()["sleep_idle_seconds"] == 0


@pytest.mark.parametrize("seconds", [30, -1, 86401, True, "300", 1.5, None])
def test_sleep_idle_command_rejects_invalid_numbers(server, seconds):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "sleep_idle", "seconds": seconds})
    assert exc.value.code == 400


def test_drive_commands_require_csrf(server):
    httpd, _ = server
    for payload in (
        {"command": "sleep"},
        {"command": "speed", "speed": 4},
        {"command": "sleep_idle", "seconds": 60},
    ):
        request = urllib.request.Request(
            url(httpd, "/api/command"),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 403


def test_sleep_blocked_while_converting(server):
    httpd, service = server
    service.update(convert_state="working")
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "sleep"})
    assert exc.value.code == 409
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(httpd, "/api/command", {"command": "speed", "speed": 4})
    assert exc.value.code == 409


def test_css_status_and_command_refusal(server):
    httpd, service = server
    service.update(
        css_protected=True,
        state="protected",
        message="Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.",
    )
    with urllib.request.urlopen(url(httpd, "/api/status")) as response:
        data = json.load(response)
    assert data["css_protected"] is True
    assert data["state"] == "protected"
    for command in ("copy", "iso"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            post_json(httpd, "/api/command", {"command": command})
        assert exc.value.code == 409
        assert "CSS-beveiligd" in json.load(exc.value)["error"]


class _DashboardParser(HTMLParser):
    """Collect the ids, labels and describedby targets the dashboard relies on."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.describedby: set[str] = set()
        self.label_for: set[str] = set()
        self.inline_events: list[str] = []
        self.inline_styles: list[str] = []
        self.scripts: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.lang = ""
        self.viewport = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.lang = attributes.get("lang", "")
        if "id" in attributes:
            self.ids.add(attributes["id"])
        for target in attributes.get("aria-describedby", "").split():
            self.describedby.add(target)
        if tag == "label" and "for" in attributes:
            self.label_for.add(attributes["for"])
        for name in attributes:
            if name.startswith("on"):
                self.inline_events.append(name)
        if "style" in attributes:
            self.inline_styles.append(attributes["style"])
        if tag == "script":
            self.scripts.append(attributes)
        if tag == "button":
            self.buttons.append(attributes)
        if tag == "meta" and attributes.get("name") == "viewport":
            self.viewport = "width=device-width" in attributes.get("content", "")


def _parsed_dashboard() -> _DashboardParser:
    parser = _DashboardParser()
    parser.feed((WEB_ROOT / "index.html").read_text(encoding="utf-8"))
    return parser


def test_static_assets_served_with_security_headers(server):
    httpd, _ = server
    expected = {
        "/": "text/html; charset=utf-8",
        "/app.js": "text/javascript; charset=utf-8",
        "/style.css": "text/css; charset=utf-8",
        "/favicon.svg": "image/svg+xml",
    }
    for path, content_type in expected.items():
        with urllib.request.urlopen(url(httpd, path)) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == content_type
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["X-Frame-Options"] == "DENY"
            csp = response.headers["Content-Security-Policy"]
            assert "unsafe-inline" not in csp
            assert "script-src 'self'" in csp
            assert response.read()


def test_dashboard_markup_accessibility_contract():
    parser = _parsed_dashboard()
    assert parser.lang == "nl"
    assert parser.viewport
    # Every aria-describedby target must exist as an id in the document.
    assert parser.describedby <= parser.ids
    # The controls the JS drives must be present.
    required = {
        "protection-banner",
        "action-reason",
        "iso-help",
        "source-status",
        "convert-reason",
        "connection-label",
        "convert-source",
        "convert-title",
        "convert-button",
        "cancel-convert",
        "refresh-sources",
        "drive-speed",
        "sleep-idle",
        "copy-button",
        "iso-button",
    }
    assert required <= parser.ids
    # No inline handlers or styles: the CSP forbids them.
    assert parser.inline_events == []
    assert parser.inline_styles == []
    # Scripts are external only.
    assert parser.scripts and all("src" in s for s in parser.scripts)
    # Buttons declare a type so they never submit anything implicitly.
    assert parser.buttons and all(b.get("type") == "button" for b in parser.buttons)
    # Range and select controls have explicit labels.
    assert {"drive-speed", "sleep-idle"} <= parser.label_for


def test_dashboard_js_references_only_existing_ids():
    parser = _parsed_dashboard()
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    referenced = set(re.findall(r"\$\(\"([a-z0-9-]+)\"\)", script))
    assert referenced
    assert referenced <= parser.ids, referenced - parser.ids
    # The hardened rendering helpers must be in place (no innerHTML / setInterval).
    assert "innerHTML" not in script
    assert "setInterval" not in script
    assert "AbortController" in script


def test_dashboard_css_has_mobile_and_focus_rules():
    css = (WEB_ROOT / "style.css").read_text(encoding="utf-8")
    assert "focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 480px)" in css
    assert ".protection-banner" in css
    assert ".action-reason" in css


def test_iso_help_states_no_decryption(server):
    httpd, _ = server
    with urllib.request.urlopen(url(httpd, "/")) as response:
        html = response.read().decode("utf-8")
    assert "ISO maken ontgrendelt of ontsleutelt niets" in html
    assert "CSS-beveiligde dvd's worden geweigerd" in html
