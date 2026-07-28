from __future__ import annotations

import ipaddress
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
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
    create_app,
)


class FakeIpmi:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], ConnectionConfig, float | None]] = []
        self.lock = threading.Lock()

    def run(
        self,
        config: ConnectionConfig,
        arguments: tuple[str, ...],
        timeout: float | None = None,
    ) -> CommandResult:
        with self.lock:
            self.calls.append((tuple(arguments), config, timeout))
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
        self.assertEqual(
            anonymous.post("/api/sensors/deep-scan", json={}).status_code, 401
        )
        self.assertEqual(anonymous.get("/api/sensors/deep-scan").status_code, 401)

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


if __name__ == "__main__":
    unittest.main()
