from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    ApiError,
    CommandResult,
    ConnectionConfig,
    IpmiRunner,
    MacAddressDiscovery,
    TelemetryStore,
    _FingerprintAdapter,
    _login_rate_key,
    _normalise_fingerprint,
    _parse_redfish_telemetry,
    _redfish_client_from_environment,
    _sample_statistics,
    create_app,
)


class FakeIpmi:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], ConnectionConfig, float | None]] = []
        self.lock = threading.Lock()
        self.deep_scan_result: CommandResult | None = None

    def run(
        self,
        config: ConnectionConfig,
        arguments: tuple[str, ...],
        timeout: float | None = None,
    ) -> CommandResult:
        with self.lock:
            self.calls.append((tuple(arguments), config, timeout))
        if tuple(arguments) == ("sdr", "elist", "all") and self.deep_scan_result:
            return self.deep_scan_result
        if tuple(arguments) == ("mc", "info"):
            stdout = "Device ID : 32\nManufacturer Name : DELL Inc."
        elif tuple(arguments) == ("sdr", "type", "Temperature"):
            stdout = "Inlet Temp | 01h | ok | 7.1 | 24 degrees C"
        elif tuple(arguments) == ("sdr", "type", "Fan"):
            stdout = "Fan1 RPM | 30h | ok | 7.1 | 4080 RPM"
        elif tuple(arguments) == ("dcmi", "power", "reading"):
            stdout = "Instantaneous power reading: 126 Watts"
        elif tuple(arguments) == ("sdr", "elist", "all"):
            stdout = (
                "Inlet Temp | 01h | ok | 7.1 | 24 degrees C\n"
                "Fan1 RPM | 30h | ok | 7.1 | 4080 RPM\n"
                "PS1 Power | 70h | ok | 10.1 | 126 Watts"
            )
        else:
            stdout = ""
        return CommandResult(0, stdout, "", 0.01)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeRedfish:
    def __init__(self, *, fail: bool = False, omit_power: bool = False) -> None:
        self.fail = fail
        self.omit_power = omit_power
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.fail:
            return FakeResponse(500, {})
        if url.endswith("/Thermal"):
            return FakeResponse(
                200,
                {
                    "Temperatures": [
                        {
                            "Name": "Inlet Temp",
                            "ReadingCelsius": 24,
                            "Status": {"Health": "OK"},
                        }
                    ],
                    "Fans": [
                        {
                            "Name": "Fan1",
                            "Reading": 4080,
                            "ReadingUnits": "RPM",
                            "Status": {"Health": "OK"},
                        }
                    ],
                },
            )
        if url.endswith("/Power"):
            if self.omit_power:
                return FakeResponse(200, {"PowerControl": []})
            return FakeResponse(
                200,
                {
                    "PowerControl": [
                        {
                            "PowerConsumedWatts": 126,
                            "PowerCapacityWatts": 750,
                            "PowerMetrics": {"AverageConsumedWatts": 119},
                        }
                    ]
                },
            )
        return FakeResponse(404, {})


class FakeIdracLoginRedfish:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if url.endswith("/redfish/v1/Managers"):
            if kwargs.get("auth") == (self.username, self.password):
                return FakeResponse(200, {"Members": []})
            return FakeResponse(401, {})
        return FakeResponse(404, {})


def configured_connection(password: str = "idrac-secret") -> ConnectionConfig:
    return ConnectionConfig(
        host="192.168.5.151",
        username="root",
        password=password,
        ipmi_port=623,
        redfish_port=443,
        redfish_verify=False,
        timeout_seconds=10,
    )


class WebBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ipmi = FakeIpmi()
        self.redfish = FakeRedfish()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "AUTH_MODE": "static",
                "WEB_USERNAME": "admin",
                "WEB_PASSWORD": "web-secret",
                "WEB_PASSWORD_HASH": "",
                "REQUIRE_ORIGIN": False,
            },
            ipmi_runner=self.ipmi,
            redfish_get=self.redfish,
        )
        self.backend = self.app.extensions["r730xd_backend"]
        self.backend.state.config = configured_connection()
        self.client = self.app.test_client()
        login = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "web-secret"},
        )
        self.assertEqual(login.status_code, 200)
        self.csrf = login.get_json()["data"]["csrf_token"]
        self.headers = {"X-CSRF-Token": self.csrf}

    def wait_for(self, predicate, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("background operation did not finish")

    def test_dashboard_and_safe_status_are_public_but_controls_are_protected(self) -> None:
        anonymous = self.app.test_client()
        status = anonymous.get("/api/status")
        self.assertEqual(status.status_code, 200)
        status_text = status.get_data(as_text=True)
        self.assertNotIn("192.168.5.151", status_text)
        self.assertNotIn("root", status_text)
        self.assertNotIn("idrac-secret", status_text)
        landing = anonymous.get("/")
        self.assertEqual(landing.status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

        self.assertEqual(anonymous.get("/api/config").status_code, 401)
        self.assertEqual(
            anonymous.post("/api/connection/test", json={}).status_code, 401
        )
        self.assertEqual(
            anonymous.post(
                "/api/control/interlock", json={"enabled": True}
            ).status_code,
            401,
        )
        # The SDR repository is read-only sensor telemetry of the same class the
        # anonymous dashboard already publishes, so reading it needs no login.
        # Writes to the machine above still do.
        self.assertIn(anonymous.get("/api/sensors/deep-scan").status_code, (200, 202))
        self.assertIn(
            anonymous.post("/api/sensors/deep-scan", json={}).status_code, (200, 202)
        )

    def test_anonymous_deep_scan_is_rate_limited_but_operators_are_not(self) -> None:
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "deep-scan-cooldown",
                "AUTH_MODE": "static",
                "WEB_USERNAME": "admin",
                "WEB_PASSWORD": "correct horse",
                "REQUIRE_ORIGIN": False,
                "DEEP_SCAN_MIN_INTERVAL": 3600,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=FakeRedfish(),
        )
        backend = app.extensions["r730xd_backend"]
        backend.state.config = configured_connection()

        anonymous = app.test_client()
        first = anonymous.post("/api/sensors/deep-scan", json={})
        self.assertIn(first.status_code, (200, 202))

        # `sdr elist all` is a heavy walk against a resource-constrained iDRAC8.
        # A second anonymous request inside the window must reuse the result
        # rather than start another walk.
        second = anonymous.post("/api/sensors/deep-scan", json={})
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["data"]["throttled"])

        operator = app.test_client()
        login = operator.post(
            "/api/auth/login",
            json={"username": "admin", "password": "correct horse"},
        )
        self.assertEqual(login.status_code, 200)
        allowed = operator.post(
            "/api/sensors/deep-scan",
            json={},
            headers={"X-CSRF-Token": login.get_json()["data"]["csrf_token"]},
        )
        self.assertIn(allowed.status_code, (200, 202))
        self.assertNotIn("throttled", allowed.get_json()["data"])

    def test_static_login_failure_remains_rate_safe(self) -> None:
        anonymous = self.app.test_client()
        wrong = anonymous.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong"}
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn("wrong", str(wrong.get_json()))

    def test_public_summary_strips_sensitive_fields_and_cannot_force_refresh(self) -> None:
        state = self.backend.state
        with state.lock:
            state.telemetry = {
                "observed_at": "2026-07-15T01:02:03Z",
                "source": "redfish",
                "temperatures": [{"name": "Inlet", "celsius": 24.0}],
                "fans": [{"name": "Fan1", "rpm": 4080.0}],
                "power": {"consumed_watts": 126.0},
                "host": "192.168.5.151",
                "username": "root",
                "password": "idrac-secret",
                "errors": ["idrac-secret"],
            }
            state.telemetry_error = "idrac-secret"
            state.telemetry_time = time.monotonic()
            original_time = state.telemetry_time

        response = self.app.test_client().get("/api/telemetry/summary?refresh=1")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertNotIn("192.168.5.151", text)
        self.assertNotIn("root", text)
        self.assertNotIn("idrac-secret", text)
        self.assertNotIn('"error"', text)
        self.assertNotIn('"errors"', text)
        with state.lock:
            self.assertEqual(state.telemetry_time, original_time)

        status = self.app.test_client().get("/api/status")
        status_text = status.get_data(as_text=True)
        self.assertEqual(status.status_code, 200)
        self.assertNotIn("idrac-secret", status_text)
        self.assertNotIn('"error"', status_text)

    def test_public_summary_failed_refresh_is_throttled_by_normal_ttl(self) -> None:
        calls = 0

        def fail_collection(_config: ConnectionConfig):
            nonlocal calls
            calls += 1
            raise ApiError(502, "telemetry_failed", "sensitive backend error")

        self.backend.collect_telemetry = fail_collection
        anonymous = self.app.test_client()
        first = anonymous.get("/api/telemetry/summary")
        self.assertEqual(first.status_code, 202)
        self.wait_for(lambda: not self.backend.state.telemetry_refreshing)
        second = anonymous.get("/api/telemetry/summary?refresh=1")
        self.assertEqual(second.status_code, 202)
        self.assertEqual(calls, 1)
        self.assertNotIn("sensitive backend error", second.get_data(as_text=True))

    def test_csrf_is_required_for_authenticated_mutations(self) -> None:
        response = self.client.post("/api/control/interlock", json={"enabled": True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "csrf_failed")

    def test_server_enforces_interlock_manual_mode_and_speed_range(self) -> None:
        locked = self.client.post(
            "/api/control/manual", json={"confirmed": True}, headers=self.headers
        )
        self.assertEqual(locked.status_code, 409)

        self.assertEqual(
            self.client.post(
                "/api/control/interlock", json={"enabled": True}, headers=self.headers
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/control/manual", json={"confirmed": True}, headers=self.headers
            ).status_code,
            200,
        )
        invalid = self.client.post(
            "/api/control/speed", json={"percent": 4}, headers=self.headers
        )
        self.assertEqual(invalid.status_code, 400)
        changed = self.client.post(
            "/api/control/speed", json={"percent": 15}, headers=self.headers
        )
        self.assertEqual(changed.status_code, 200)
        self.assertIn(
            ("raw", "0x30", "0x30", "0x02", "0xff", "0x0f"),
            [call[0] for call in self.ipmi.calls],
        )

        restored = self.client.post("/api/control/auto", json={}, headers=self.headers)
        self.assertEqual(restored.status_code, 200)
        control = restored.get_json()["data"]["control"]
        self.assertEqual(control["mode"], "auto")
        self.assertFalse(control["safety_unlocked"])

    def test_connection_test_returns_structured_device_data(self) -> None:
        response = self.client.post(
            "/api/connection/test", json={}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["device"]["Device ID"], "32")

    def test_redfish_telemetry_is_background_cached_and_password_safe(self) -> None:
        first = self.client.get("/api/telemetry/summary")
        self.assertEqual(first.status_code, 202)
        self.wait_for(lambda: not self.backend.state.telemetry_refreshing)
        second = self.client.get("/api/telemetry/summary")
        self.assertEqual(second.status_code, 200)
        payload = second.get_json()["data"]
        self.assertEqual(payload["telemetry"]["source"], "redfish")
        self.assertEqual(payload["telemetry"]["temperatures"][0]["celsius"], 24.0)
        self.assertEqual(payload["telemetry"]["fans"][0]["rpm"], 4080.0)
        self.assertEqual(payload["telemetry"]["power"]["consumed_watts"], 126.0)

        calls_before = len(self.redfish.calls)
        self.client.get("/api/telemetry/summary")
        self.assertEqual(len(self.redfish.calls), calls_before)
        for url, kwargs in self.redfish.calls:
            self.assertNotIn("idrac-secret", url)
            self.assertFalse(kwargs["verify"])
            self.assertEqual(kwargs["auth"], ("root", "idrac-secret"))

    def test_redfish_failure_falls_back_without_full_sdr_scan(self) -> None:
        self.backend.redfish_get = FakeRedfish(fail=True)
        result = self.backend.collect_telemetry(configured_connection())
        self.assertEqual(result["source"], "ipmitool")
        self.assertEqual(result["temperatures"][0]["celsius"], 24.0)
        self.assertEqual(result["fans"][0]["rpm"], 4080.0)
        self.assertEqual(result["power"]["consumed_watts"], 126.0)
        arguments = [call[0] for call in self.ipmi.calls]
        self.assertNotIn(("sdr", "elist", "all"), arguments)

    def test_partial_redfish_is_filled_by_typed_ipmi_queries(self) -> None:
        self.backend.redfish_get = FakeRedfish(omit_power=True)
        result = self.backend.collect_telemetry(configured_connection())
        self.assertEqual(result["source"], "redfish+ipmitool")
        self.assertEqual(result["temperatures"][0]["celsius"], 24.0)
        self.assertEqual(result["power"]["consumed_watts"], 126.0)
        self.assertNotIn(
            ("sdr", "elist", "all"), [call[0] for call in self.ipmi.calls]
        )

    def test_full_sdr_elist_is_only_used_by_manual_deep_scan(self) -> None:
        started = self.client.post(
            "/api/sensors/deep-scan", json={}, headers=self.headers
        )
        self.assertEqual(started.status_code, 202)
        self.wait_for(lambda: self.backend.state.deep_scan.get("status") != "running")
        result = self.client.get("/api/sensors/deep-scan")
        self.assertEqual(result.status_code, 200)
        data = result.get_json()["data"]
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["result"]["summary"]["total"], 3)
        self.assertIn(("sdr", "elist", "all"), [call[0] for call in self.ipmi.calls])

    def test_deep_scan_keeps_records_when_ipmitool_dies_with_sigsegv(self) -> None:
        output = (
            "Inlet Temp | 01h | ok | 7.1 | 24 degrees C\n"
            "Fan1 RPM | 30h | ok | 7.1 | 4080 RPM"
        )
        for returncode in (-11, 139):
            with self.subTest(returncode=returncode):
                self.ipmi.deep_scan_result = CommandResult(
                    returncode, output, "Segmentation fault", 0.05
                )
                started = self.client.post(
                    "/api/sensors/deep-scan", json={}, headers=self.headers
                )
                self.assertEqual(started.status_code, 202)
                self.wait_for(
                    lambda: self.backend.state.deep_scan.get("status") != "running"
                )
                data = self.client.get("/api/sensors/deep-scan").get_json()["data"]
                self.assertEqual(data["status"], "complete")
                self.assertEqual(data["result"]["summary"]["total"], 2)
                self.assertTrue(data["result"]["partial"])
                self.assertEqual(
                    data["result"]["partial_reason"], "ipmitool_sigsegv"
                )

    def test_deep_scan_does_not_mask_other_nonzero_exits(self) -> None:
        self.ipmi.deep_scan_result = CommandResult(
            1,
            "Inlet Temp | 01h | ok | 7.1 | 24 degrees C",
            "authentication failed",
            0.05,
        )
        started = self.client.post(
            "/api/sensors/deep-scan", json={}, headers=self.headers
        )
        self.assertEqual(started.status_code, 202)
        self.wait_for(lambda: self.backend.state.deep_scan.get("status") != "running")
        data = self.client.get("/api/sensors/deep-scan").get_json()["data"]
        self.assertEqual(data["status"], "error")

    def test_deep_scan_requires_records_even_after_sigsegv(self) -> None:
        self.ipmi.deep_scan_result = CommandResult(
            -11, "", "Segmentation fault", 0.05
        )
        started = self.client.post(
            "/api/sensors/deep-scan", json={}, headers=self.headers
        )
        self.assertEqual(started.status_code, 202)
        self.wait_for(lambda: self.backend.state.deep_scan.get("status") != "running")
        data = self.client.get("/api/sensors/deep-scan").get_json()["data"]
        self.assertEqual(data["status"], "error")

    def test_config_response_never_contains_password(self) -> None:
        response = self.client.get("/api/config")
        text = response.get_data(as_text=True)
        self.assertNotIn("idrac-secret", text)
        self.assertTrue(response.get_json()["data"]["connection"]["password_set"])


class MacAddressDiscoveryTests(unittest.TestCase):
    target_mac = "b8:ca:3a:12:34:56"

    @staticmethod
    def arp_text(*rows: str) -> str:
        return (
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            + "\n".join(rows)
            + "\n"
        )

    def test_arp_parser_accepts_only_complete_exact_mac_and_safe_ipv4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(
                self.arp_text(
                    "192.168.5.80 0x1 0x2 00:11:22:33:44:55 * br-lan",
                    f"192.168.5.81 0x1 0x0 {self.target_mac} * br-lan",
                    f"127.0.0.1 0x1 0x2 {self.target_mac} * br-lan",
                    f"192.168.5.207 0x1 0x2 {self.target_mac.upper()} * br-lan",
                    f"192.168.5.208 0x1 0x2 {self.target_mac} * docker0",
                    "not-an-ip 0x1 0x2 b8:ca:3a:12:34:56 * br-lan",
                    "192.168.5.90 broken row",
                ),
                encoding="ascii",
            )
            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                network="192.168.5.0/24",
                interface="br-lan",
                scanner=lambda _network, _timeout: ["192.168.5.207"],
            )

            self.assertEqual(discovery.resolve("192.168.5.151"), "192.168.5.207")
            self.assertEqual(
                discovery._read_matching_addresses(
                    ipaddress.ip_network("192.168.5.0/24")
                ),
                ["192.168.5.207"],
            )

    def test_missing_entry_uses_bounded_unauthenticated_scan_then_exact_arp_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(self.arp_text(), encoding="ascii")
            scans: list[tuple[str, float]] = []

            def scan(network, timeout):
                scans.append((str(network), timeout))
                arp_file.write_text(
                    self.arp_text(
                        f"192.168.5.207 0x1 0x2 {self.target_mac} * br-lan"
                    ),
                    encoding="ascii",
                )
                return ["192.168.5.207"]

            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                scanner=scan,
                sleeper=lambda _seconds: None,
            )

            self.assertEqual(discovery.resolve("192.168.5.151"), "192.168.5.207")
            self.assertEqual(
                scans,
                [("192.168.5.151/32", 0.6), ("192.168.5.0/24", 0.6)],
            )
            # Every privileged use actively revalidates the current /32.
            self.assertEqual(discovery.resolve("192.168.5.207"), "192.168.5.207")
            self.assertEqual(scans[-1], ("192.168.5.207/32", 0.6))

    def test_empty_discovery_is_rate_limited_and_keeps_last_known_address(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(self.arp_text(), encoding="ascii")
            scans: list[str] = []
            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                scan_interval=60,
                scanner=lambda network, _timeout: scans.append(str(network)) or [],
                monotonic=lambda: 100.0,
                sleeper=lambda _seconds: None,
            )

            self.assertIsNone(discovery.resolve("192.168.5.151"))
            self.assertIsNone(discovery.resolve("192.168.5.151"))
            self.assertEqual(
                scans,
                ["192.168.5.151/32", "192.168.5.0/24", "192.168.5.151/32"],
            )

    def test_forced_scan_disambiguates_stale_old_entry_by_mac_and_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(
                self.arp_text(
                    f"192.168.5.151 0x1 0x2 {self.target_mac} * br-lan"
                ),
                encoding="ascii",
            )

            def scan(_network, _timeout):
                arp_file.write_text(
                    self.arp_text(
                        f"192.168.5.151 0x1 0x2 {self.target_mac} * br-lan",
                        f"192.168.5.207 0x1 0x2 {self.target_mac} * br-lan",
                    ),
                    encoding="ascii",
                )
                return ["192.168.5.207"]

            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                scanner=scan,
                sleeper=lambda _seconds: None,
            )
            self.assertEqual(
                discovery.resolve("192.168.5.151", force_scan=True),
                "192.168.5.207",
            )

    def test_strict_discovery_never_accepts_arp_without_current_probe_pong(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(
                self.arp_text(
                    f"192.168.5.151 0x1 0x2 {self.target_mac} * br-lan"
                ),
                encoding="ascii",
            )
            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                network="192.168.5.0/24",
                interface="br-lan",
                scanner=lambda _network, _timeout: [],
                sleeper=lambda _seconds: None,
            )
            self.assertIsNone(
                discovery.resolve("192.168.5.151", force_scan=True, strict=True)
            )

    def test_discovery_rejects_non_rfc1918_cidr_and_strictly_validates_pong(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(self.arp_text(), encoding="ascii")
            with self.assertRaisesRegex(ValueError, "RFC1918"):
                MacAddressDiscovery(
                    self.target_mac,
                    str(arp_file),
                    network="203.0.113.0/24",
                )

        tag = 0x37
        pong = (
            bytes.fromhex("06 00 ff 06 00 00 11 be 40")
            + bytes((tag, 0x00, 0x10))
            + bytes(16)
        )
        self.assertEqual(
            MacAddressDiscovery._valid_asf_pong(
                pong, ("192.168.5.111", 623), tag
            ),
            "192.168.5.111",
        )
        self.assertIsNone(
            MacAddressDiscovery._valid_asf_pong(
                pong, ("192.168.5.111", 624), tag
            )
        )
        self.assertIsNone(
            MacAddressDiscovery._valid_asf_pong(
                pong, ("192.168.5.111", 623), tag + 1
            )
        )
        self.assertIsNone(
            MacAddressDiscovery._valid_asf_pong(
                pong[:-1], ("192.168.5.111", 623), tag
            )
        )
        wrong_type = bytearray(pong)
        wrong_type[8] = 0x41
        self.assertIsNone(
            MacAddressDiscovery._valid_asf_pong(
                bytes(wrong_type), ("192.168.5.111", 623), tag
            )
        )

    def test_endpoint_update_preserves_credentials_and_hardware_uses_only_mac_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(
                self.arp_text(
                    f"192.168.5.207 0x1 0x2 {self.target_mac} * br-lan"
                ),
                encoding="ascii",
            )
            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                network="192.168.5.0/24",
                scanner=lambda network, _timeout: (
                    ["192.168.5.207"] if network.prefixlen == 24 else []
                ),
                sleeper=lambda _seconds: None,
            )
            ipmi = FakeIpmi()
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "mac-discovery-test-key",
                    "AUTH_MODE": "static",
                    "WEB_USERNAME": "admin",
                    "WEB_PASSWORD": "web-secret",
                    "REQUIRE_ORIGIN": False,
                },
                ipmi_runner=ipmi,
                redfish_get=FakeRedfish(),
                mac_discovery=discovery,
            )
            backend = app.extensions["r730xd_backend"]
            backend.state.config = configured_connection("preserved-secret")
            client = app.test_client()
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "web-secret"},
            )
            csrf = login.get_json()["data"]["csrf_token"]

            response = client.post(
                "/api/connection/test",
                json={},
                headers={"X-CSRF-Token": csrf},
            )

            self.assertEqual(response.status_code, 200)
            config, revision = backend.state.connection_snapshot()
            self.assertEqual(config.host, "192.168.5.207")
            self.assertEqual(config.username, "root")
            self.assertEqual(config.password, "preserved-secret")
            self.assertEqual(revision, 1)
            self.assertEqual([call[1].host for call in ipmi.calls], ["192.168.5.207"])
            self.assertEqual(
                [call[1].password for call in ipmi.calls], ["preserved-secret"]
            )

    def test_telemetry_switches_to_verified_mac_before_any_credentialed_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            arp_file.write_text(
                self.arp_text(
                    f"192.168.5.151 0x1 0x2 {self.target_mac} * br-lan"
                ),
                encoding="ascii",
            )
            scan_count = 0

            def scan(_network, _timeout):
                nonlocal scan_count
                scan_count += 1
                arp_file.write_text(
                    self.arp_text(
                        f"192.168.5.207 0x1 0x2 {self.target_mac} * br-lan"
                    ),
                    encoding="ascii",
                )
                return ["192.168.5.207"]

            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                scanner=scan,
                sleeper=lambda _seconds: None,
            )
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "mac-retry-test-key",
                    "AUTH_MODE": "static",
                    "REQUIRE_ORIGIN": False,
                },
                ipmi_runner=FakeIpmi(),
                redfish_get=FakeRedfish(),
                mac_discovery=discovery,
            )
            backend = app.extensions["r730xd_backend"]
            backend.state.config = configured_connection("preserved-secret")
            attempted_hosts: list[str] = []

            def collect(config: ConnectionConfig):
                attempted_hosts.append(config.host)
                if config.host == "192.168.5.151":
                    raise ApiError(502, "old_endpoint_failed", "old endpoint failed")
                return {
                    "observed_at": "2026-07-15T01:02:03Z",
                    "source": "redfish",
                    "temperatures": [{"name": "Inlet", "celsius": 24.0}],
                    "fans": [{"name": "Fan1", "rpm": 4080.0}],
                    "power": {"consumed_watts": 126.0},
                    "alerts": [],
                    "errors": [],
                }

            backend.collect_telemetry = collect
            response = app.test_client().get("/api/telemetry/summary")
            self.assertEqual(response.status_code, 202)
            deadline = time.monotonic() + 1.0
            while backend.state.telemetry_refreshing and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertFalse(backend.state.telemetry_refreshing)
            # The stale address never receives a password-bearing telemetry call.
            self.assertEqual(attempted_hosts, ["192.168.5.207"])
            self.assertEqual(scan_count, 2)  # last-known /32, then bounded CIDR
            config, _revision = backend.state.connection_snapshot()
            self.assertEqual(config.host, "192.168.5.207")
            self.assertEqual(config.password, "preserved-secret")

    def test_unverified_identity_returns_503_with_zero_hardware_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arp_file = Path(directory) / "arp"
            # Even a matching ARP row is insufficient without this operation's Pong.
            arp_file.write_text(
                self.arp_text(
                    f"192.168.5.151 0x1 0x2 {self.target_mac} * br-lan"
                ),
                encoding="ascii",
            )
            discovery = MacAddressDiscovery(
                self.target_mac,
                str(arp_file),
                network="192.168.5.0/24",
                interface="br-lan",
                scanner=lambda _network, _timeout: [],
                sleeper=lambda _seconds: None,
            )
            ipmi = FakeIpmi()
            redfish = FakeRedfish()
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "fail-closed-test-key",
                    "AUTH_MODE": "static",
                    "WEB_USERNAME": "admin",
                    "WEB_PASSWORD": "web-secret",
                    "REQUIRE_ORIGIN": False,
                },
                ipmi_runner=ipmi,
                redfish_get=redfish,
                mac_discovery=discovery,
            )
            backend = app.extensions["r730xd_backend"]
            backend.state.config = configured_connection("must-not-be-sent")
            client = app.test_client()
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "web-secret"},
            )
            headers = {
                "X-CSRF-Token": login.get_json()["data"]["csrf_token"]
            }

            self.assertEqual(
                client.post("/api/connection/test", json={}, headers=headers).status_code,
                503,
            )
            client.post(
                "/api/control/interlock",
                json={"enabled": True},
                headers=headers,
            )
            self.assertEqual(
                client.post(
                    "/api/control/manual",
                    json={"confirmed": True},
                    headers=headers,
                ).status_code,
                503,
            )
            self.assertEqual(
                client.post("/api/control/auto", json={}, headers=headers).status_code,
                503,
            )
            self.assertEqual(
                client.post(
                    "/api/control/speed",
                    json={"percent": 15},
                    headers=headers,
                ).status_code,
                503,
            )
            self.assertEqual(
                client.post(
                    "/api/sensors/deep-scan", json={}, headers=headers
                ).status_code,
                503,
            )

            summary = client.get("/api/telemetry/summary")
            self.assertEqual(summary.status_code, 202)
            deadline = time.monotonic() + 1.0
            while backend.state.telemetry_refreshing and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(backend.state.telemetry_refreshing)
            self.assertEqual(ipmi.calls, [])
            self.assertEqual(redfish.calls, [])


class SecurityPrimitiveTests(unittest.TestCase):
    def test_production_rejects_placeholder_or_short_session_secret(self) -> None:
        for weak_secret in ("replace-with-at-least-32-random-bytes", "too-short"):
            with self.subTest(secret=weak_secret):
                with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                    create_app(
                        {"TESTING": False, "SECRET_KEY": weak_secret},
                        ipmi_runner=FakeIpmi(),
                        redfish_get=FakeRedfish(),
                    )

    def test_ipmitool_uses_environment_not_process_arguments(self) -> None:
        captured: dict[str, Any] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        runner = IpmiRunner("/usr/bin/ipmitool", process_runner=fake_run)
        runner.run(configured_connection(), ("mc", "info"))
        self.assertNotIn("idrac-secret", captured["command"])
        self.assertNotIn("-P", captured["command"])
        self.assertIn("-E", captured["command"])
        self.assertEqual(captured["kwargs"]["env"]["IPMI_PASSWORD"], "idrac-secret")
        self.assertNotIn("WEB_PASSWORD", captured["kwargs"]["env"])
        self.assertNotIn("FLASK_SECRET_KEY", captured["kwargs"]["env"])
        self.assertFalse(captured["kwargs"]["shell"])

    def test_timeout_is_structured(self) -> None:
        def timed_out(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("ipmitool", 1)

        runner = IpmiRunner(process_runner=timed_out)
        with self.assertRaises(ApiError) as raised:
            runner.run(configured_connection(), ("mc", "info"), timeout=1)
        self.assertEqual(raised.exception.code, "ipmi_timeout")

    def test_cross_origin_login_is_rejected(self) -> None:
        app = create_app(
            {
                "TESTING": False,
                "SECRET_KEY": "cross-origin-test-secret-32-bytes!",
                "AUTH_MODE": "static",
                "WEB_USERNAME": "admin",
                "WEB_PASSWORD": "secret",
                "REQUIRE_ORIGIN": True,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=FakeRedfish(),
        )
        client = app.test_client()
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        allowed = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(allowed.status_code, 200)


class TelemetryHistoryTests(unittest.TestCase):
    def test_history_is_public_bounded_oldest_first_and_summary_only(self) -> None:
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "history-test-key",
                "AUTH_MODE": "static",
                "HISTORY_MAX_SAMPLES": 3,
                "REQUIRE_ORIGIN": False,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=FakeRedfish(),
        )
        backend = app.extensions["r730xd_backend"]
        backend.state.config = configured_connection()
        config, revision = backend.state.connection_snapshot()
        payloads = [
            {
                "observed_at": f"2026-07-15T00:00:0{index}Z",
                "source": "redfish",
                "temperatures": [
                    {"name": "CPU secret raw sensor", "celsius": 20 + index},
                    {"name": "Inlet", "celsius": 10 + index},
                ],
                "fans": [
                    {"name": "Fan1", "rpm": 3000 + index * 100},
                    {"name": "Fan2", "rpm": 4000 + index * 100},
                ],
                "power": {"consumed_watts": 100 + index},
                "errors": ["raw error must not be retained"],
            }
            for index in range(1, 5)
        ]
        for payload in payloads:
            backend.collect_telemetry = lambda _config, item=payload: item
            backend._refresh_telemetry(config, revision)

        response = app.test_client().get("/api/telemetry/history")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(len(data["samples"]), 3)
        self.assertEqual(
            [sample["timestamp"] for sample in data["samples"]],
            [
                "2026-07-15T00:00:02Z",
                "2026-07-15T00:00:03Z",
                "2026-07-15T00:00:04Z",
            ],
        )
        self.assertEqual(data["current"]["timestamp"], "2026-07-15T00:00:04Z")
        self.assertEqual(data["previous"]["timestamp"], "2026-07-15T00:00:03Z")
        self.assertEqual(data["previous2"]["timestamp"], "2026-07-15T00:00:02Z")
        self.assertEqual(data["current"]["max_temp_c"], 24.0)
        self.assertEqual(data["current"]["avg_fan_rpm"], 3900.0)
        self.assertEqual(data["current"]["power_watts"], 104.0)
        self.assertEqual(
            set(data["current"]),
            {
                "timestamp",
                "max_temp_c",
                "avg_fan_rpm",
                "power_watts",
                "source",
            },
        )
        text = response.get_data(as_text=True)
        self.assertNotIn("CPU secret raw sensor", text)
        self.assertNotIn("raw error", text)


class IdracAuthenticationTests(unittest.TestCase):
    def create_idrac_app(
        self, redfish_get: Any, configured_password: str = ""
    ):
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "idrac-auth-test-key",
                "AUTH_MODE": "idrac",
                "WEB_PASSWORD": "",
                "WEB_PASSWORD_HASH": "",
                "REQUIRE_ORIGIN": False,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=redfish_get,
        )
        app.extensions["r730xd_backend"].state.config = configured_connection(
            configured_password
        )
        return app

    def test_idrac_login_verifies_managers_and_stores_verified_credentials(self) -> None:
        redfish = FakeIdracLoginRedfish("root", "real-idrac-secret")
        app = self.create_idrac_app(redfish)
        client = app.test_client()

        response = client.post(
            "/api/auth/login",
            json={"username": "root", "password": "real-idrac-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["username"], "root")
        self.assertEqual(len(redfish.calls), 1)
        url, kwargs = redfish.calls[0]
        self.assertEqual(
            url, "https://192.168.5.151:443/redfish/v1/Managers"
        )
        self.assertEqual(kwargs["auth"], ("root", "real-idrac-secret"))
        config, _revision = app.extensions[
            "r730xd_backend"
        ].state.connection_snapshot()
        self.assertEqual(config.username, "root")
        self.assertEqual(config.password, "real-idrac-secret")
        self.assertNotIn("real-idrac-secret", response.get_data(as_text=True))
        self.assertNotIn(
            "real-idrac-secret", response.headers.get("Set-Cookie", "")
        )

    def test_idrac_wrong_password_is_invalid_login_and_rate_limited(self) -> None:
        redfish = FakeIdracLoginRedfish("root", "real-idrac-secret")
        app = self.create_idrac_app(redfish, "previous-secret")
        client = app.test_client()

        for _attempt in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": "root", "password": "wrong-idrac-secret"},
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json()["error"]["code"], "invalid_login")
            self.assertNotIn(
                "wrong-idrac-secret", response.get_data(as_text=True)
            )
        limited = client.post(
            "/api/auth/login",
            json={"username": "root", "password": "wrong-idrac-secret"},
        )
        self.assertEqual(limited.status_code, 429)
        config, _revision = app.extensions[
            "r730xd_backend"
        ].state.connection_snapshot()
        self.assertEqual(config.password, "previous-secret")
        self.assertEqual(redfish.calls, [])

    def test_configured_idrac_credentials_are_checked_locally(self) -> None:
        redfish = FakeIdracLoginRedfish("root", "previous-secret")
        app = self.create_idrac_app(redfish, "previous-secret")

        response = app.test_client().post(
            "/api/auth/login",
            json={"username": "root", "password": "previous-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(redfish.calls, [])

    def test_idrac_password_is_not_exposed_by_service_errors_or_logs(self) -> None:
        password = "never-echo-this-idrac-secret"

        def unavailable(_url: str, **_kwargs: Any):
            raise requests.ConnectionError(f"network failed while using {password}")

        app = self.create_idrac_app(unavailable)
        client = app.test_client()
        captured: list[str] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(self.format(record))

        handler = CaptureHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            response = client.post(
                "/api/auth/login",
                json={"username": "root", "password": password},
            )
        finally:
            root_logger.removeHandler(handler)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"]["code"], "redfish_unavailable")
        self.assertNotIn(password, response.get_data(as_text=True))
        self.assertNotIn(password, response.headers.get("Set-Cookie", ""))
        self.assertNotIn(password, "\n".join(captured))
        config, _revision = app.extensions[
            "r730xd_backend"
        ].state.connection_snapshot()
        self.assertEqual(config.password, "")

    def test_password_file_initializes_the_single_idrac_credential(self) -> None:
        redfish = FakeIdracLoginRedfish("root", "secret-from-file")
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / "idrac_password"
            secret_path.write_text("secret-from-file\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "IDRAC_PASSWORD_FILE": str(secret_path),
                    "IDRAC_PASSWORD": "ignored-environment-secret",
                },
            ):
                app = create_app(
                    {
                        "TESTING": True,
                        "SECRET_KEY": "password-file-test",
                        "AUTH_MODE": "idrac",
                        "REQUIRE_ORIGIN": False,
                    },
                    ipmi_runner=FakeIpmi(),
                    redfish_get=redfish,
                )

        config, _revision = app.extensions[
            "r730xd_backend"
        ].state.connection_snapshot()
        self.assertEqual(config.password, "secret-from-file")
        response = app.test_client().post(
            "/api/auth/login",
            json={"username": "root", "password": "secret-from-file"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(redfish.calls, [])
        self.assertNotIn("secret-from-file", response.get_data(as_text=True))


def _self_signed_https_server():
    """A throwaway TLS server that records whether it ever saw credentials."""

    import hashlib
    import http.server
    import socketserver
    import ssl
    from datetime import timedelta as _timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _timedelta(days=1))
        .not_valid_after(now + _timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    der = certificate.public_bytes(serialization.Encoding.DER)
    fingerprint = hashlib.sha256(der).hexdigest()

    directory = tempfile.mkdtemp()
    cert_path = Path(directory) / "cert.pem"
    cert_path.write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            header = self.headers.get("Authorization")
            if header:
                seen.append(header)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    class QuietServer(socketserver.TCPServer):
        daemon_threads = True

        def handle_error(self, request, client_address) -> None:
            # A client that rejects the pin drops the connection mid-handshake.
            # That is the behaviour under test, not a server fault, so keep it
            # out of the test output.
            return

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path)
    server = QuietServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.received_authorization = seen
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, fingerprint


class PublicSurfaceInvariantTests(unittest.TestCase):
    """The one invariant the whole security model rests on.

    Every route is either read-only-and-public or it changes the machine and
    needs iDRAC credentials. That split currently holds, but nothing stopped a
    future change from quietly moving a write onto the public side, so assert
    it behaviourally: drive every public route anonymously and prove no IPMI
    write command reaches the BMC.
    """

    WRITE_PREFIXES = (("raw", "0x30", "0x30", "0x01"), ("raw", "0x30", "0x30", "0x02"))

    def _app(self):
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "public-surface-invariant",
                "AUTH_MODE": "static",
                "WEB_USERNAME": "admin",
                "WEB_PASSWORD": "correct horse",
                "REQUIRE_ORIGIN": False,
                "DEEP_SCAN_MIN_INTERVAL": 0,
            },
            ipmi_runner=self.ipmi,
            redfish_get=FakeRedfish(),
        )
        app.extensions["r730xd_backend"].state.config = configured_connection()
        return app

    def setUp(self) -> None:
        self.ipmi = FakeIpmi()

    def _public_rules(self, app):
        public = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            view = app.view_functions[rule.endpoint]
            # login_required wraps the view; the wrapper keeps __wrapped__.
            if getattr(view, "__wrapped__", None) is not None:
                continue
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                public.append((method, str(rule.rule)))
        return public

    def test_no_public_route_can_issue_an_ipmi_write(self) -> None:
        app = self._app()
        client = app.test_client()
        public = self._public_rules(app)
        self.assertGreater(len(public), 5, "public surface unexpectedly empty")

        for method, path in public:
            with self.subTest(route=f"{method} {path}"):
                if method == "GET":
                    client.get(path)
                else:
                    client.post(path, json={})

        time.sleep(0.4)  # let any background deep-scan / telemetry thread finish
        writes = [
            args
            for args, _config, _timeout in self.ipmi.calls
            if args[:4] in self.WRITE_PREFIXES
        ]
        self.assertEqual(
            writes,
            [],
            f"a public route issued an IPMI write to the BMC: {writes}",
        )

    def test_every_machine_write_route_is_credential_gated(self) -> None:
        app = self._app()
        gated = {
            str(rule.rule)
            for rule in app.url_map.iter_rules()
            if rule.endpoint != "static"
            and getattr(app.view_functions[rule.endpoint], "__wrapped__", None)
            is not None
        }
        for path in ("/api/control/manual", "/api/control/auto", "/api/control/speed"):
            self.assertIn(path, gated, f"{path} changes the machine and must be gated")


class LoginRateKeyTests(unittest.TestCase):
    def test_attempts_are_bucketed_by_network_not_by_address(self) -> None:
        # Handing yourself another address on a LAN is free, so a per-address
        # budget was not a budget at all.
        self.assertEqual(
            _login_rate_key("192.168.5.10"), _login_rate_key("192.168.5.200")
        )
        self.assertNotEqual(
            _login_rate_key("192.168.5.10"), _login_rate_key("192.168.6.10")
        )
        self.assertEqual(_login_rate_key(None), "unknown")
        self.assertEqual(_login_rate_key("not-an-ip"), "not-an-ip")

    def test_rotating_addresses_inside_one_subnet_cannot_reset_the_budget(self) -> None:
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "login-rate-subnet",
                "AUTH_MODE": "static",
                "WEB_USERNAME": "admin",
                "WEB_PASSWORD": "correct horse",
                "REQUIRE_ORIGIN": False,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=FakeRedfish(),
        )
        client = app.test_client()
        statuses = [
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": f"wrong-{index}"},
                environ_base={"REMOTE_ADDR": f"192.168.5.{20 + index}"},
            ).status_code
            for index in range(7)
        ]
        self.assertEqual(statuses[:5], [401] * 5)
        self.assertEqual(statuses[5:], [429, 429])

        # A different /24 keeps its own budget.
        other = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
            environ_base={"REMOTE_ADDR": "192.168.9.5"},
        )
        self.assertEqual(other.status_code, 401)


class RedfishFingerprintPinningTests(unittest.TestCase):
    FINGERPRINT = "ab" * 32

    def test_accepts_colon_separated_and_bare_hex(self) -> None:
        colons = ":".join("ab" for _ in range(32)).upper()
        self.assertEqual(_normalise_fingerprint(colons), self.FINGERPRINT)
        self.assertEqual(_normalise_fingerprint(self.FINGERPRINT), self.FINGERPRINT)

    def test_rejects_anything_that_is_not_sha256(self) -> None:
        for bad in ("", "deadbeef", "zz" * 32, "ab" * 31, "ab" * 33):
            with self.subTest(value=bad), self.assertRaises(RuntimeError):
                _normalise_fingerprint(bad)

    def test_client_pins_the_certificate_when_configured(self) -> None:
        with patch.dict(os.environ, {"REDFISH_TLS_FINGERPRINT": self.FINGERPRINT}):
            client = _redfish_client_from_environment()
        adapter = client.__self__.get_adapter("https://192.168.5.151/redfish/v1")
        self.assertIsInstance(adapter, _FingerprintAdapter)
        self.assertEqual(
            adapter.poolmanager.connection_pool_kw.get("assert_fingerprint"),
            self.FINGERPRINT,
        )

    def test_pin_mismatch_aborts_before_credentials_are_sent(self) -> None:
        """End-to-end proof against a real self-signed TLS server.

        This is the property that matters: with a wrong pin the handshake must
        fail, so the Basic-auth header carrying the iDRAC password is never
        written to the impostor. Asserting only on kwargs would not show that.
        """

        server, fingerprint = _self_signed_https_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"https://127.0.0.1:{server.server_address[1]}/redfish/v1"
        auth = ("root", "super-secret-idrac-password")

        with patch.dict(os.environ, {"REDFISH_TLS_FINGERPRINT": "cd" * 32}):
            wrong = _redfish_client_from_environment()
        with self.assertRaises(requests.exceptions.RequestException):
            wrong(url, auth=auth, verify=False, timeout=5)
        self.assertEqual(
            server.received_authorization,
            [],
            "credentials reached a server that failed the pin",
        )

        with patch.dict(os.environ, {"REDFISH_TLS_FINGERPRINT": fingerprint}):
            correct = _redfish_client_from_environment()
        response = correct(url, auth=auth, verify=False, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(server.received_authorization), 1)

    def test_unset_fingerprint_keeps_the_plain_client(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REDFISH_TLS_FINGERPRINT", None)
            self.assertIs(_redfish_client_from_environment(), requests.get)


class ImpossiblePowerMetricsTests(unittest.TestCase):
    """iDRAC8 2.70 returns a live wattage plus an all-zero PowerMetrics block."""

    @staticmethod
    def _payload(consumed, average, minimum, maximum):
        return {
            "PowerControl": [
                {
                    "PowerConsumedWatts": consumed,
                    "PowerCapacityWatts": 896,
                    "PowerAllocatedWatts": 896,
                    "PowerMetrics": {
                        "AverageConsumedWatts": average,
                        "MinConsumedWatts": minimum,
                        "MaxConsumedWatts": maximum,
                    },
                }
            ]
        }

    def test_all_zero_metrics_beside_a_live_reading_are_dropped(self) -> None:
        power = _parse_redfish_telemetry({}, self._payload(135, 0, 0, 0))["power"]
        self.assertEqual(power["consumed_watts"], 135.0)
        # Capacity and allocation are unrelated fields and must survive.
        self.assertEqual(power["capacity_watts"], 896.0)
        self.assertEqual(power["allocated_watts"], 896.0)
        for key in ("average_watts", "minimum_watts", "maximum_watts"):
            self.assertNotIn(key, power, f"{key} is not a measurement here")

    def test_genuine_metrics_are_preserved(self) -> None:
        power = _parse_redfish_telemetry({}, self._payload(135, 133, 128, 142))["power"]
        self.assertEqual(power["average_watts"], 133.0)
        self.assertEqual(power["minimum_watts"], 128.0)
        self.assertEqual(power["maximum_watts"], 142.0)

    def test_a_genuinely_idle_chassis_keeps_its_zeros(self) -> None:
        # consumed == 0 makes all-zero metrics plausible, so do not discard.
        power = _parse_redfish_telemetry({}, self._payload(0, 0, 0, 0))["power"]
        self.assertEqual(power["average_watts"], 0.0)
        self.assertEqual(power["minimum_watts"], 0.0)
        self.assertEqual(power["maximum_watts"], 0.0)

    def test_a_partially_zero_block_is_left_alone(self) -> None:
        power = _parse_redfish_telemetry({}, self._payload(135, 0, 0, 142))["power"]
        self.assertEqual(power["maximum_watts"], 142.0)
        self.assertEqual(power["average_watts"], 0.0)


class SampleStatisticsTests(unittest.TestCase):
    def test_nulls_are_ignored_and_do_not_skew_the_average(self) -> None:
        samples = [
            {"power_watts": 131},
            {"power_watts": None},
            {"power_watts": 138},
            {},
            {"power_watts": 133},
        ]
        stats = _sample_statistics(samples, "power_watts")
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["minimum"], 131)
        self.assertEqual(stats["maximum"], 138)
        self.assertEqual(stats["average"], 134.0)

    def test_no_usable_samples_reports_zero_count_not_zero_watts(self) -> None:
        stats = _sample_statistics([{"power_watts": None}, {}], "power_watts")
        self.assertEqual(
            stats, {"count": 0, "average": None, "minimum": None, "maximum": None}
        )

    def test_history_response_carries_statistics_for_every_series(self) -> None:
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "history-statistics",
                "AUTH_MODE": "static",
                "HISTORY_MAX_SAMPLES": 10,
                "REQUIRE_ORIGIN": False,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=FakeRedfish(),
        )
        backend = app.extensions["r730xd_backend"]
        backend.state.config = configured_connection()
        config, revision = backend.state.connection_snapshot()
        for index, watts in enumerate((131, 135, 138)):
            payload = {
                "observed_at": f"2026-08-16T00:00:0{index}Z",
                "source": "redfish",
                "temperatures": [{"name": "CPU1", "celsius": 40 + index}],
                "fans": [{"name": "Fan1", "rpm": 4000 + index}],
                "power": {"consumed_watts": watts},
            }
            backend.collect_telemetry = lambda _config, item=payload: item
            backend._refresh_telemetry(config, revision)

        data = app.test_client().get("/api/telemetry/history").get_json()["data"]
        power = data["statistics"]["power_watts"]
        self.assertEqual(power["count"], 3)
        self.assertEqual(power["minimum"], 131)
        self.assertEqual(power["maximum"], 138)
        self.assertEqual(power["average"], 134.67)
        self.assertEqual(data["statistics"]["max_temp_c"]["maximum"], 42)
        self.assertEqual(data["statistics"]["avg_fan_rpm"]["count"], 3)


class TelemetryStoreTests(unittest.TestCase):
    @staticmethod
    def _stamp(offset_seconds: int) -> str:
        moment = datetime.now(UTC) - timedelta(seconds=offset_seconds)
        return moment.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _store(self, **kwargs: Any) -> TelemetryStore:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        kwargs.setdefault("flush_interval", 0.0)
        kwargs.setdefault("flush_threshold", 1)
        store = TelemetryStore(str(Path(directory) / "nested" / "telemetry.db"), **kwargs)
        self.addCleanup(store.close)
        return store

    def test_records_survive_and_bucket_by_range(self) -> None:
        store = self._store()
        self.assertTrue(store.enabled)
        for index in range(240):
            store.record(
                {
                    "timestamp": self._stamp(15 * (240 - index)),
                    "max_temp_c": 50.0 + (index % 10),
                    "avg_fan_rpm": 4000.0 + index,
                    "power_watts": 170.0,
                    "source": "redfish",
                }
            )
        store.flush()
        self.assertEqual(store.stats()["rows"], 240)

        five_minutes = store.samples(300)
        self.assertTrue(0 < len(five_minutes) <= 25)
        # A 24 h window must be downsampled, never returned row-for-row.
        day = store.samples(86400)
        self.assertLessEqual(len(day), 240)
        self.assertEqual(
            set(day[0]),
            {"timestamp", "max_temp_c", "avg_fan_rpm", "power_watts", "source"},
        )
        self.assertEqual(
            [item["timestamp"] for item in day],
            sorted(item["timestamp"] for item in day),
        )

    def test_duplicate_timestamps_replace_instead_of_accumulating(self) -> None:
        store = self._store()
        stamp = self._stamp(30)
        store.record({"timestamp": stamp, "max_temp_c": 40.0, "source": "redfish"})
        store.record({"timestamp": stamp, "max_temp_c": 41.0, "source": "redfish"})
        store.flush()
        self.assertEqual(store.stats()["rows"], 1)

    def test_retention_prunes_rows_older_than_window(self) -> None:
        store = self._store(retention_days=1)
        store.record({"timestamp": self._stamp(86400 * 5), "max_temp_c": 30.0})
        store.record({"timestamp": self._stamp(60), "max_temp_c": 31.0})
        store.flush()
        self.assertEqual(store.stats()["rows"], 1)

    def test_unwritable_path_disables_store_without_raising(self) -> None:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        blocker = directory / "blocker"
        blocker.write_text("a file where a directory is needed", encoding="utf-8")

        store = TelemetryStore(str(blocker / "nested" / "telemetry.db"))
        self.addCleanup(store.close)
        self.assertFalse(store.enabled)
        self.assertIsNotNone(store.error)
        store.record({"timestamp": self._stamp(10), "max_temp_c": 20.0})
        self.assertEqual(store.flush(), 0)
        self.assertEqual(store.samples(3600), [])
        self.assertFalse(store.stats()["enabled"])


class TelemetryHistoryRangeTests(unittest.TestCase):
    def _app(self, database_path: str):
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "history-range-key",
                "AUTH_MODE": "static",
                "HISTORY_MAX_SAMPLES": 5,
                "REQUIRE_ORIGIN": False,
                "TELEMETRY_DB_PATH": database_path,
                "TELEMETRY_FLUSH_INTERVAL": 0.0,
                "TELEMETRY_FLUSH_THRESHOLD": 1,
            },
            ipmi_runner=FakeIpmi(),
            redfish_get=FakeRedfish(),
        )
        store = app.extensions.get("telemetry_store")
        if store is not None:
            self.addCleanup(store.close)
        return app

    def _feed(self, app, count: int) -> None:
        backend = app.extensions["r730xd_backend"]
        backend.state.config = configured_connection()
        config, revision = backend.state.connection_snapshot()
        for index in range(count):
            moment = datetime.now(UTC) - timedelta(seconds=15 * (count - index))
            payload = {
                "observed_at": moment.isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                ),
                "source": "redfish",
                "temperatures": [{"name": "CPU", "celsius": 50 + index}],
                "fans": [{"name": "Fan1", "rpm": 4000 + index}],
                "power": {"consumed_watts": 170 + index},
            }
            backend.collect_telemetry = lambda _config, item=payload: item
            backend._refresh_telemetry(config, revision)

    def test_long_range_is_served_from_sqlite_beyond_the_deque(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        app = self._app(str(Path(directory) / "telemetry.db"))
        self._feed(app, 40)

        client = app.test_client()
        default = client.get("/api/telemetry/history").get_json()["data"]
        # Without ?range the deque bound still applies, unchanged from before.
        self.assertEqual(len(default["samples"]), 5)
        self.assertEqual(default["source"], "memory")
        self.assertTrue(default["persistence"]["enabled"])

        ranged = client.get("/api/telemetry/history?range=1h").get_json()["data"]
        self.assertEqual(ranged["source"], "sqlite")
        self.assertGreater(len(ranged["samples"]), 5)
        # current/previous must stay on live samples, not bucket averages.
        self.assertEqual(ranged["current"], default["current"])

    def test_unknown_range_is_rejected(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        app = self._app(str(Path(directory) / "telemetry.db"))
        response = app.test_client().get("/api/telemetry/history?range=99y")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_range")

    def test_history_still_works_without_a_database(self) -> None:
        app = self._app("")
        self.assertIsNone(app.extensions.get("telemetry_store"))
        self._feed(app, 3)
        data = app.test_client().get("/api/telemetry/history?range=24h").get_json()[
            "data"
        ]
        self.assertEqual(data["source"], "memory")
        self.assertFalse(data["persistence"]["enabled"])
        self.assertEqual(len(data["samples"]), 3)


if __name__ == "__main__":
    unittest.main()
