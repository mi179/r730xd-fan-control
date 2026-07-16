"""Non-destructive smoke test for the deployed Web console.

This validates anonymous monitoring, iDRAC authentication and API permission
wiring. It deliberately does not send any fan-control command.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def assert_secret_absent(value: object, secret: str) -> None:
    if isinstance(value, dict):
        assert all(str(key).casefold() != "password" for key in value)
        for item in value.values():
            assert_secret_absent(item, secret)
    elif isinstance(value, list):
        for item in value:
            assert_secret_absent(item, secret)
    elif isinstance(value, str):
        assert value != secret


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    settings = read_env(env_path)
    base_url = os.getenv(
        "R730XD_WEB_URL",
        f"http://192.168.5.2:{settings.get('WEB_PORT', '8088')}",
    ).rstrip("/")
    origin = base_url

    idrac_user = settings.get("IDRAC_USER", "root")
    secret_path = env_path.parent / "secrets" / "idrac_password"
    idrac_password = secret_path.read_text(encoding="utf-8").rstrip("\r\n")
    if not idrac_password:
        raise RuntimeError("secrets/idrac_password is empty")

    client = requests.Session()
    health = client.get(f"{base_url}/healthz", timeout=5)
    health.raise_for_status()
    assert health.json()["data"]["status"] == "healthy"

    dashboard = client.get(f"{base_url}/", timeout=5)
    dashboard.raise_for_status()
    for marker in ("temperatureValue", "fanValue", "powerValue", "controlGate"):
        assert marker in dashboard.text

    anonymous_session = client.get(f"{base_url}/api/auth/session", timeout=5)
    anonymous_session.raise_for_status()
    assert anonymous_session.json()["data"]["authenticated"] is False
    public_status = client.get(f"{base_url}/api/status", timeout=5)
    public_summary = client.get(f"{base_url}/api/telemetry/summary", timeout=5)
    public_history = client.get(f"{base_url}/api/telemetry/history", timeout=5)
    assert public_status.ok
    assert public_summary.status_code in {200, 202}
    assert public_history.ok
    for response in (public_status, public_summary, public_history):
        assert settings.get("IDRAC_HOST", "192.168.5.151") not in response.text
        assert_secret_absent(response.json(), idrac_password)
    assert client.get(f"{base_url}/api/config", timeout=5).status_code == 401
    blocked_control = client.post(
        f"{base_url}/api/control/interlock",
        json={"enabled": True},
        headers={"Origin": origin},
        timeout=5,
    )
    assert blocked_control.status_code == 401

    login = client.post(
        f"{base_url}/api/auth/login",
        json={"username": idrac_user, "password": idrac_password},
        headers={"Origin": origin},
        timeout=5,
    )
    login.raise_for_status()
    login_data = login.json()["data"]
    csrf_token = login_data["csrf_token"]

    config = client.get(f"{base_url}/api/config", timeout=5)
    config.raise_for_status()
    assert_secret_absent(config.json(), idrac_password)
    assert "idrac_password" not in config.text.casefold()

    logout = client.post(
        f"{base_url}/api/auth/logout",
        json={},
        headers={"Origin": origin, "X-CSRF-Token": csrf_token},
        timeout=5,
    )
    logout.raise_for_status()

    print(f"LIVE_SMOKE_OK {base_url} public-monitor/login/protected-config/logout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
