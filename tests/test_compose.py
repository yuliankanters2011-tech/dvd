from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_compose_security_and_propagation():
    data = yaml.safe_load((ROOT / "compose.yaml").read_text())
    service = data["services"]["dvd"]
    assert service.get("privileged") is not True
    assert "SYS_ADMIN" in service["cap_add"]
    assert service["devices"] == ["${DEVICE:-/dev/sr0}:/dev/sr0:r"]
    assert service["environment"]["SLEEP_IDLE_SECONDS"] == "${SLEEP_IDLE_SECONDS:-600}"
    assert service["environment"]["DRIVE_SPEED"] == "${DRIVE_SPEED:-0}"
    bind = next(v for v in service["volumes"] if isinstance(v, dict) and v["target"] == "/srv/dvd")
    assert bind["bind"]["propagation"] == "rshared"
    assert service["ports"] == ["${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8080}:8080"]


def test_compose_jellyfin_mounts_and_converter_env():
    for filename in ("compose.yaml", "docker-compose.yml"):
        data = yaml.safe_load((ROOT / filename).read_text())
        svc = data["services"]["dvd"]
        env = svc["environment"]
        assert env["FILMS_DIR"] == "/volume1/video/Films"
        assert env["MUSIC_DIR"] == "/volume1/video/Muziek"
        assert env["CONVERT_THREADS"] == "${CONVERT_THREADS:-2}"
        assert env["CONVERT_CRF"] == "${CONVERT_CRF:-20}"
        assert env["CONVERT_PRESET"] == "${CONVERT_PRESET:-veryfast}"
        assert env["CONVERT_AUDIO_BITRATE"] == "${CONVERT_AUDIO_BITRATE:-192}"
        assert env["ISO_MAX_MISSING_PERCENT"] == "${ISO_MAX_MISSING_PERCENT:-0.5}"
        assert env["SLEEP_IDLE_SECONDS"] == "${SLEEP_IDLE_SECONDS:-600}"
        assert env["DRIVE_SPEED"] == "${DRIVE_SPEED:-0}"
        films = next(
            v
            for v in svc["volumes"]
            if isinstance(v, dict) and v["target"] == "/volume1/video/Films"
        )
        music = next(
            v
            for v in svc["volumes"]
            if isinstance(v, dict) and v["target"] == "/volume1/video/Muziek"
        )
        assert films["source"] == "${FILMS_PATH:-/volume1/video/Films}"
        assert music["source"] == "${MUSIC_PATH:-/volume1/video/Muziek}"
        assert films.get("read_only") is not True
        assert music.get("read_only") is not True


def test_dockerfile_workdir_and_packages():
    text = (ROOT / "Dockerfile").read_text()
    assert "WORKDIR /opt/dvd-bridge" in text
    assert "python3 util-linux ddrescue ffmpeg" in text
    assert 'CMD ["python3", "-m", "app.main"]' in text


def test_install_compose_uses_published_image_only():
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    service = data["services"]["dvd"]
    assert "build" not in service
    assert (
        service["image"] == "${IMAGE_NAME:-ghcr.io/yuliankanters2011-tech/dvd}:${IMAGE_TAG:-latest}"
    )
    assert service.get("privileged") is not True
    bind = next(v for v in service["volumes"] if isinstance(v, dict) and v["target"] == "/srv/dvd")
    assert bind["bind"]["propagation"] == "rshared"


def test_dockerhub_workflow_requires_secrets():
    data = yaml.safe_load((ROOT / ".github/workflows/dockerhub.yml").read_text())
    steps = data["jobs"]["publish"]["steps"]
    text = "\n".join(str(step.get("run", "")) + str(step.get("with", "")) for step in steps)
    assert "DOCKERHUB_USERNAME" in text
    assert "DOCKERHUB_TOKEN" in text
    assert "docker.io" in text
