"""Read-only live verification through the deployed Web API.

The iDRAC password is read interactively and is never written to disk or
placed in a process argument. This script does not call any control endpoint.
"""

from __future__ import annotations

import getpass
import os
import sys
import time
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


def api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("error", {}).get("message") or response.status_code)
    except ValueError:
        return f"HTTP {response.status_code}"


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    settings = read_env(env_path)
    base_url = os.getenv(
        "R730XD_WEB_URL",
        f"http://192.168.5.2:{settings.get('WEB_PORT', '8088')}",
    ).rstrip("/")
    origin = base_url
    secret_file = os.getenv("IDRAC_PASSWORD_FILE", "").strip()
    if secret_file:
        idrac_password = Path(secret_file).read_text(encoding="utf-8").rstrip("\r\n")
    else:
        idrac_password = getpass.getpass("iDRAC password (input hidden): ")
    if not idrac_password:
        raise RuntimeError("iDRAC password cannot be empty")

    client = requests.Session()
    client.trust_env = False
    login = client.post(
        f"{base_url}/api/auth/login",
        json={
            "username": settings.get("IDRAC_USER", "root"),
            "password": idrac_password,
        },
        headers={"Origin": origin},
        timeout=8,
    )
    if not login.ok:
        raise RuntimeError(f"Web login failed: {api_error(login)}")
    csrf = login.json()["data"]["csrf_token"]
    mutation_headers = {"Origin": origin, "X-CSRF-Token": csrf}

    try:
        started = time.monotonic()
        connection = client.post(
            f"{base_url}/api/connection/test",
            json={},
            headers=mutation_headers,
            timeout=18,
        )
        if not connection.ok:
            raise RuntimeError(f"IPMI connection failed: {api_error(connection)}")
        connection_data = connection.json()["data"]
        print(
            "CONNECTION_OK",
            f"elapsed={connection_data.get('elapsed_seconds')}s",
            f"device_fields={len(connection_data.get('device', {}))}",
        )

        summary = client.get(
            f"{base_url}/api/telemetry/summary?refresh=1",
            timeout=8,
        )
        if summary.status_code not in {200, 202}:
            raise RuntimeError(f"Telemetry start failed: {api_error(summary)}")

        telemetry = None
        response_data: dict[str, object] = {}
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            response_data = summary.json()["data"]
            telemetry = response_data.get("telemetry")
            if telemetry is not None and not response_data.get("refreshing"):
                break
            time.sleep(1)
            summary = client.get(f"{base_url}/api/telemetry/summary", timeout=8)
            if summary.status_code not in {200, 202}:
                raise RuntimeError(f"Telemetry failed: {api_error(summary)}")
        if not isinstance(telemetry, dict):
            raise RuntimeError(
                f"Telemetry timed out: {response_data.get('error') or 'no data'}"
            )

        temperatures = telemetry.get("temperatures") or []
        fans = telemetry.get("fans") or []
        power = telemetry.get("power") or {}
        temperature_values = [
            item.get("celsius")
            for item in temperatures
            if isinstance(item, dict) and item.get("celsius") is not None
        ]
        fan_values = [
            item.get("rpm")
            for item in fans
            if isinstance(item, dict) and item.get("rpm") is not None
        ]
        print(
            "TELEMETRY_OK",
            f"source={telemetry.get('source')}",
            f"elapsed={round(time.monotonic() - started, 2)}s",
            f"temperatures={len(temperatures)}",
            f"max_c={max(temperature_values) if temperature_values else 'n/a'}",
            f"fans={len(fans)}",
            f"rpm_range={min(fan_values) if fan_values else 'n/a'}-"
            f"{max(fan_values) if fan_values else 'n/a'}",
            f"watts={power.get('consumed_watts', 'n/a') if isinstance(power, dict) else 'n/a'}",
        )
        return 0
    finally:
        client.post(
            f"{base_url}/api/auth/logout",
            json={},
            headers=mutation_headers,
            timeout=8,
        )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"READONLY_CHECK_FAILED {exc}", file=sys.stderr)
        sys.exit(1)
