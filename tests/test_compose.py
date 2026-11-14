from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_compose_security_and_propagation():
    data = yaml.safe_load((ROOT / "compose.yaml").read_text())
    service = data["services"]["dvd"]
    assert service.get("privileged") is not True
    assert "SYS_ADMIN" in service["cap_add"]
    assert service["devices"] == ["${DEVICE:-/dev/sr0}:/dev/sr0:r"]
    bind = next(v for v in service["volumes"] if isinstance(v, dict) and v["target"] == "/srv/dvd")
    assert bind["bind"]["propagation"] == "rshared"
    assert service["ports"] == ["${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8080}:8080"]


def test_dockerfile_workdir_and_packages():
    text = (ROOT / "Dockerfile").read_text()
    assert "WORKDIR /opt/dvd-bridge" in text
    assert "python3 util-linux ddrescue" in text
    assert 'CMD ["python3", "-m", "app.main"]' in text
