from __future__ import annotations

import hmac
import ipaddress
import os
import re
import secrets
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import warnings
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, redirect, render_template, request, session
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from werkzeug.security import check_password_hash

MANUAL_MODE_RAW = ("raw", "0x30", "0x30", "0x01", "0x00")
AUTO_MODE_RAW = ("raw", "0x30", "0x30", "0x01", "0x01")
SAFE_OUTPUT_LIMIT = 512 * 1024
HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
MAC_PATTERN = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
ARP_FILE_LIMIT = 512 * 1024
SIGSEGV_RETURNCODES = {-signal.SIGSEGV, 128 + signal.SIGSEGV}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _json_ok(data: Mapping[str, Any] | None = None, status: int = 200):
    return jsonify({"ok": True, "data": dict(data or {})}), status


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    host: str
    username: str
    password: str
    ipmi_port: int
    redfish_port: int
    redfish_verify: bool | str
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.password)

    def public_dict(self) -> dict[str, Any]:
        if isinstance(self.redfish_verify, str):
            tls_mode = "custom_ca"
        elif self.redfish_verify:
            tls_mode = "verify"
        else:
            tls_mode = "idrac_self_signed"
        return {
            "configured": self.configured,
            "host": self.host,
            "username": self.username,
            "password_set": bool(self.password),
            "ipmi_port": self.ipmi_port,
            "redfish_port": self.redfish_port,
            "tls_mode": tls_mode,
            "redfish_verify_tls": self.redfish_verify is not False,
        }


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class IpmiRunner:
    """Run ipmitool without placing the iDRAC password in argv or logs."""

    def __init__(
        self,
        executable: str = "/usr/bin/ipmitool",
        process_runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.executable = executable
        self._process_runner = process_runner

    def run(
        self,
        config: ConnectionConfig,
        arguments: Sequence[str],
        timeout: float | None = None,
    ) -> CommandResult:
        _require_connection(config)
        command = [
            self.executable,
            "-I",
            "lanplus",
            "-H",
            config.host,
            "-p",
            str(config.ipmi_port),
            "-U",
            config.username,
            "-E",
            *arguments,
        ]
        # Do not pass the Web login secret, Flask signing key, or unrelated
        # container secrets into the child process environment.
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
            if key in os.environ
        }
        environment["IPMI_PASSWORD"] = config.password
        started = time.monotonic()
        try:
            completed = self._process_runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or config.timeout_seconds,
                env=environment,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ApiError(504, "ipmi_timeout", "iDRAC 命令执行超时") from exc
        except OSError as exc:
            raise ApiError(503, "ipmitool_unavailable", "容器内无法启动 ipmitool") from exc
        return CommandResult(
            returncode=int(completed.returncode),
            stdout=_redact_and_limit(completed.stdout or "", config.password),
            stderr=_redact_and_limit(completed.stderr or "", config.password),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )


def _login_rate_key(remote_addr: str | None) -> str:
    """Bucket login attempts by network, not by single address.

    In `idrac` auth mode a wrong password is compared locally so that guessing
    never burns the iDRAC's own small remote-failure budget (D-005). The upside
    is that the real BMC account cannot be locked out by an attacker; the
    downside is that this endpoint is the *only* thing standing between a LAN
    attacker and an unlimited offline-style guessing loop. Keying on the exact
    source address made that trivial to sidestep — every fresh IP got a fresh
    budget, and handing out extra addresses on a LAN costs nothing.
    """

    raw = (remote_addr or "").strip()
    if not raw:
        return "unknown"
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return raw[:64]
    prefix = 24 if isinstance(address, ipaddress.IPv4Address) else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


class LoginLimiter:
    def __init__(self, maximum: int = 5, window_seconds: int = 300) -> None:
        self.maximum = maximum
        self.window_seconds = window_seconds
        self._attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] > self.window_seconds:
                attempts.popleft()
            return len(attempts) >= self.maximum

    def failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(time.monotonic())

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


class MacAddressDiscovery:
    """Actively verify one known iDRAC MAC without sending any credentials."""

    def __init__(
        self,
        mac: str,
        arp_file: str,
        *,
        network: str = "",
        interface: str = "",
        scan_interval: float = 60.0,
        probe_timeout: float = 0.6,
        max_hosts: int = 256,
        scanner: Callable[[ipaddress.IPv4Network, float], Sequence[str]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.mac = self._normalise_mac(mac)
        self.arp_file = Path(arp_file)
        self.network = self._parse_network(network) if network.strip() else None
        self.interface = str(interface or "").strip()
        if self.interface and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", self.interface):
            raise ValueError("IDRAC_ARP_INTERFACE is invalid")
        self.scan_interval = min(3600.0, max(15.0, float(scan_interval)))
        self.probe_timeout = min(2.0, max(0.1, float(probe_timeout)))
        self.max_hosts = min(1024, max(1, int(max_hosts)))
        if self.network is not None and self.network.num_addresses > self.max_hosts:
            raise ValueError("iDRAC discovery network exceeds the host limit")
        self._scanner = scanner or self._probe_rmcp_presence
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_scan = float("-inf")
        self._lock = threading.Lock()

    @staticmethod
    def _normalise_mac(value: str) -> str:
        candidate = str(value or "").strip().lower().replace("-", ":")
        if not MAC_PATTERN.fullmatch(candidate):
            raise ValueError("IDRAC_MAC must contain exactly six hexadecimal octets")
        raw = bytes(int(part, 16) for part in candidate.split(":"))
        if raw == b"\x00" * 6 or raw == b"\xff" * 6 or raw[0] & 1:
            raise ValueError("IDRAC_MAC must be a unicast hardware address")
        return candidate

    @staticmethod
    def _parse_network(value: str) -> ipaddress.IPv4Network:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            raise ValueError("IDRAC_DISCOVERY_CIDR is invalid") from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValueError("IDRAC discovery supports IPv4 only")
        rfc1918 = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        if not any(network.subnet_of(private) for private in rfc1918):
            raise ValueError("IDRAC discovery network must be inside RFC1918 IPv4")
        return network

    @staticmethod
    def _valid_candidate_ip(value: str) -> str | None:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return None
        if not isinstance(address, ipaddress.IPv4Address):
            return None
        if (
            address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
        ):
            return None
        return str(address)

    def _read_matching_addresses(
        self, network: ipaddress.IPv4Network
    ) -> list[str]:
        try:
            with self.arp_file.open("r", encoding="ascii", errors="strict") as handle:
                text = handle.read(ARP_FILE_LIMIT + 1)
        except (OSError, UnicodeError):
            return []
        if len(text) > ARP_FILE_LIMIT:
            return []

        matches: list[str] = []
        for line in text.splitlines()[:8192]:
            fields = line.split()
            if len(fields) < 6:
                continue
            try:
                flags = int(fields[2], 0)
                mac = self._normalise_mac(fields[3])
            except (ValueError, TypeError):
                continue
            # Linux marks a resolved, usable neighbour with ATF_COM (0x02).
            if (
                not flags & 0x02
                or mac != self.mac
                or (self.interface and fields[5] != self.interface)
            ):
                continue
            candidate = self._valid_candidate_ip(fields[0])
            if (
                candidate is not None
                and ipaddress.ip_address(candidate) in network
                and candidate not in matches
            ):
                matches.append(candidate)
        return matches

    def _network_for(self, last_known_host: str) -> ipaddress.IPv4Network | None:
        if self.network is not None:
            return self.network
        try:
            address = ipaddress.ip_address(last_known_host)
        except ValueError:
            return None
        if not isinstance(address, ipaddress.IPv4Address):
            return None
        # Dell management networks are normally small LANs.  /24 is bounded,
        # avoids a dangerous broad scan, and can be overridden explicitly.
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        try:
            network = self._parse_network(str(network))
        except ValueError:
            return None
        return network if network.num_addresses <= self.max_hosts else None

    @staticmethod
    def _valid_asf_pong(payload: bytes, peer: tuple[Any, ...], tag: int) -> str | None:
        if len(peer) < 2 or peer[1] != 623 or len(payload) != 28:
            return None
        if (
            payload[0:4] != b"\x06\x00\xff\x06"
            or payload[4:8] != b"\x00\x00\x11\xbe"
            or payload[8] != 0x40
            or payload[9] != tag
            or payload[10] != 0x00
            or payload[11] != 0x10
            or len(payload) != 12 + payload[11]
        ):
            return None
        return MacAddressDiscovery._valid_candidate_ip(str(peer[0]))

    @staticmethod
    def _probe_rmcp_presence(
        network: ipaddress.IPv4Network, timeout: float
    ) -> Sequence[str]:
        responders: list[str] = []
        tag = secrets.randbelow(256)
        presence_ping = bytes.fromhex("06 00 ff 06 00 00 11 be 80") + bytes(
            (tag, 0x00, 0x00)
        )
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.setblocking(False)
                probe.bind(("0.0.0.0", 0))
                for address in network.hosts():
                    try:
                        probe.sendto(presence_ping, (str(address), 623))
                    except OSError:
                        continue
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    readable, _writable, _exceptional = select.select(
                        [probe], [], [], remaining
                    )
                    if not readable:
                        break
                    try:
                        payload, peer = probe.recvfrom(2048)
                    except OSError:
                        continue
                    candidate = MacAddressDiscovery._valid_asf_pong(payload, peer, tag)
                    if (
                        candidate is not None
                        and ipaddress.ip_address(candidate) in network
                        and candidate not in responders
                    ):
                        responders.append(candidate)
                        if network.prefixlen == 32:
                            break
        except OSError:
            return []
        return responders

    def _active_match(self, network: ipaddress.IPv4Network) -> str | None:
        try:
            responders = {
                candidate
                for value in self._scanner(network, self.probe_timeout)
                if (candidate := self._valid_candidate_ip(str(value))) is not None
                and ipaddress.ip_address(candidate) in network
            }
        except Exception:
            responders = set()
        matches = self._read_matching_addresses(network)
        if not matches:
            self._sleeper(0.05)
            matches = self._read_matching_addresses(network)
        verified = [candidate for candidate in matches if candidate in responders]
        return verified[0] if len(verified) == 1 else None

    def resolve(
        self,
        last_known_host: str,
        *,
        force_scan: bool = False,
        strict: bool = True,
    ) -> str | None:
        """Actively prove IP + exact MAC; never trust an ARP row by itself."""

        del strict  # Kept explicit at callers; all discovery is fail-closed.
        with self._lock:
            network = self._network_for(last_known_host)
            if network is None or network.num_addresses > self.max_hosts:
                return None
            current = self._valid_candidate_ip(last_known_host)
            if current is not None and ipaddress.ip_address(current) in network:
                verified_current = self._active_match(
                    ipaddress.ip_network(f"{current}/32", strict=False)
                )
                if verified_current == current:
                    return current

            # Only a failed /32 verification reaches the broader bounded scan.
            if (
                not force_scan
                and self._monotonic() - self._last_scan < self.scan_interval
            ):
                return None
            self._last_scan = self._monotonic()
            return self._active_match(network)


class TelemetryStore:
    """Durable telemetry history in SQLite.

    The in-memory deque in RuntimeState stays the hot path for the dashboard;
    this store exists so history survives a container restart and so ranges
    longer than the deque can hold remain answerable.

    Three properties matter more than throughput here:

    * **Optional.** If the path is missing, unwritable or corrupt, the store
      disables itself and the app keeps serving from memory. A read-only
      container without a mounted volume must still boot.
    * **Batched.** Samples are buffered and flushed on a size/time threshold
      rather than once per sample, so a 15 s sampling interval does not mean a
      disk write every 15 s.
    * **Bounded.** Old rows are pruned on a retention window, and long ranges
      are downsampled in SQL so a phone never receives 5 760 rows for 24 h.
    """

    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS telemetry_sample ("
        " observed_at TEXT PRIMARY KEY,"
        " max_temp_c REAL,"
        " avg_fan_rpm REAL,"
        " power_watts REAL,"
        " source TEXT)",
        "CREATE INDEX IF NOT EXISTS telemetry_sample_time"
        " ON telemetry_sample (observed_at)",
    )

    def __init__(
        self,
        path: str,
        *,
        retention_days: int = 30,
        flush_interval: float = 60.0,
        flush_threshold: int = 20,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.retention_days = max(1, int(retention_days))
        self.flush_interval = max(1.0, float(flush_interval))
        self.flush_threshold = max(1, int(flush_threshold))
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []
        self._last_flush = monotonic()
        self._last_prune = 0.0
        self.enabled = False
        self.error: str | None = None
        self._connection: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path, check_same_thread=False, timeout=5.0
            )
            # WAL keeps the writer from blocking dashboard reads; NORMAL trades
            # a crash-window of the last transaction for far fewer fsyncs.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=5000")
            for statement in self.SCHEMA:
                connection.execute(statement)
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._connection = None
            self.enabled = False
            return
        self._connection = connection
        self.enabled = True
        self.error = None

    def record(self, sample: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._pending.append(dict(sample))
            due = (
                len(self._pending) >= self.flush_threshold
                or self._monotonic() - self._last_flush >= self.flush_interval
            )
        if due:
            self.flush()

    def flush(self) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            batch, self._pending = self._pending, []
            self._last_flush = self._monotonic()
            connection = self._connection
            if connection is None or not batch:
                return 0
            rows = [
                (
                    str(item.get("timestamp") or ""),
                    item.get("max_temp_c"),
                    item.get("avg_fan_rpm"),
                    item.get("power_watts"),
                    str(item.get("source") or "unknown"),
                )
                for item in batch
                if item.get("timestamp")
            ]
            try:
                connection.executemany(
                    "INSERT OR REPLACE INTO telemetry_sample"
                    " (observed_at, max_temp_c, avg_fan_rpm, power_watts, source)"
                    " VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                connection.commit()
            except sqlite3.Error as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return 0
        self._prune()
        return len(rows)

    def _prune(self) -> None:
        now = self._monotonic()
        with self._lock:
            if self._last_prune and now - self._last_prune < 3600.0:
                return
            self._last_prune = now
            connection = self._connection
            if connection is None:
                return
            cutoff = (
                datetime.now(UTC) - timedelta(days=self.retention_days)
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
            try:
                connection.execute(
                    "DELETE FROM telemetry_sample WHERE observed_at < ?", (cutoff,)
                )
                connection.commit()
            except sqlite3.Error as exc:
                self.error = f"{type(exc).__name__}: {exc}"

    def samples(self, seconds: int, limit: int = 240) -> list[dict[str, Any]]:
        """Return at most `limit` evenly bucketed samples over the window."""

        if not self.enabled:
            return []
        self.flush()
        start = (
            datetime.now(UTC) - timedelta(seconds=max(60, int(seconds)))
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        bucket = max(1, int(seconds) // max(1, int(limit)))
        with self._lock:
            connection = self._connection
            if connection is None:
                return []
            try:
                cursor = connection.execute(
                    # strftime('%s') on the ISO timestamp gives epoch seconds;
                    # integer division buckets them, and MAX/AVG match how the
                    # dashboard reads each metric (hottest sensor, mean RPM).
                    "SELECT"
                    "  MAX(observed_at) AS observed_at,"
                    "  MAX(max_temp_c) AS max_temp_c,"
                    "  AVG(avg_fan_rpm) AS avg_fan_rpm,"
                    "  AVG(power_watts) AS power_watts,"
                    "  MAX(source) AS source"
                    " FROM telemetry_sample"
                    " WHERE observed_at >= ?"
                    " GROUP BY CAST(strftime('%s', observed_at) AS INTEGER) / ?"
                    " ORDER BY observed_at",
                    (start, bucket),
                )
                rows = cursor.fetchall()
            except sqlite3.Error as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return []
        return [
            {
                "timestamp": row[0],
                "max_temp_c": _number(row[1]),
                "avg_fan_rpm": _number(row[2]),
                "power_watts": _number(row[3]),
                "source": row[4] or "unknown",
            }
            for row in rows
        ]

    def stats(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "error": self.error}
        with self._lock:
            connection = self._connection
            if connection is None:
                return {"enabled": False, "error": self.error}
            try:
                total, oldest, newest = connection.execute(
                    "SELECT COUNT(*), MIN(observed_at), MAX(observed_at)"
                    " FROM telemetry_sample"
                ).fetchone()
            except sqlite3.Error as exc:
                return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}
        return {
            "enabled": True,
            "rows": total,
            "oldest": oldest,
            "newest": newest,
            "retention_days": self.retention_days,
        }

    def close(self) -> None:
        self.flush()
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self.enabled = False


class RuntimeState:
    """One-process state store. Gunicorn must run one worker with multiple threads."""

    def __init__(
        self,
        config: ConnectionConfig,
        cache_ttl: float,
        history_max_samples: int = 90,
        store: TelemetryStore | None = None,
    ) -> None:
        self.store = store
        self.lock = threading.RLock()
        self.control_lock = threading.RLock()
        self.config = config
        self.config_revision = 0
        self.mode = "unknown"
        self.safety_unlocked = False
        self.percent: int | None = None
        self.cache_ttl = min(10.0, max(5.0, cache_ttl))
        self.telemetry: dict[str, Any] | None = None
        self.telemetry_time = 0.0
        self.telemetry_attempt_time = 0.0
        self.telemetry_refreshing = False
        self.telemetry_error: str | None = None
        self.history: deque[dict[str, Any]] = deque(
            maxlen=min(10_000, max(1, int(history_max_samples)))
        )
        self.deep_scan: dict[str, Any] = {"status": "idle"}
        self.deep_scan_started = 0.0

    def connection_snapshot(self) -> tuple[ConnectionConfig, int]:
        with self.lock:
            return self.config, self.config_revision

    def update_discovered_host(self, host: str) -> bool:
        """Atomically move the endpoint while retaining the same iDRAC identity."""

        validated_host = _validate_host(host)
        with self.control_lock:
            with self.lock:
                if validated_host == self.config.host:
                    return False
                # The address was accepted only through an exact target-MAC ARP
                # match.  Preserve credentials and all non-address settings.
                self.config = replace(self.config, host=validated_host)
                self.config_revision += 1
                self.telemetry_time = 0.0
                self.telemetry_attempt_time = 0.0
                self.telemetry_error = None
                if self.deep_scan.get("status") == "running":
                    self.deep_scan = {"status": "idle"}
                return True

    def control_public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "mode": self.mode,
                "safety_unlocked": self.safety_unlocked,
                "percent": self.percent,
            }

    def replace_config(self, new_config: ConnectionConfig) -> None:
        with self.control_lock:
            with self.lock:
                endpoint_changed = (
                    self.config.host,
                    self.config.ipmi_port,
                    self.config.redfish_port,
                ) != (
                    new_config.host,
                    new_config.ipmi_port,
                    new_config.redfish_port,
                )
                self.config = new_config
                self.config_revision += 1
                self.mode = "unknown"
                self.safety_unlocked = False
                self.percent = None
                self.telemetry = None
                self.telemetry_time = 0.0
                self.telemetry_attempt_time = 0.0
                self.telemetry_error = None
                if endpoint_changed:
                    self.history.clear()
                self.deep_scan = {"status": "idle"}


class Backend:
    def __init__(
        self,
        state: RuntimeState,
        ipmi: Any,
        redfish_get: Callable[..., Any] = requests.get,
        chassis_id: str = "System.Embedded.1",
        mac_discovery: MacAddressDiscovery | None = None,
    ) -> None:
        self.state = state
        self.ipmi = ipmi
        self.redfish_get = redfish_get
        self.chassis_id = chassis_id
        self.mac_discovery = mac_discovery

    def refresh_endpoint(
        self,
        *,
        force_scan: bool = False,
        require_verified: bool = False,
    ) -> tuple[ConnectionConfig, int]:
        """Refresh the current endpoint without ever authenticating to candidates."""

        config, _revision = self.state.connection_snapshot()
        if self.mac_discovery is not None:
            discovered = self.mac_discovery.resolve(
                config.host,
                force_scan=force_scan,
                strict=require_verified,
            )
            if discovered is None:
                if require_verified:
                    raise ApiError(
                        503,
                        "idrac_identity_unverified",
                        "无法通过 RMCP 与 MAC 安全确认 iDRAC 地址",
                    )
            else:
                self.state.update_discovered_host(discovered)
        return self.state.connection_snapshot()

    def redfish_json(self, config: ConnectionConfig, path: str) -> dict[str, Any]:
        _require_connection(config)
        safe_path = _normalise_redfish_path(path)
        url = f"https://{config.host}:{config.redfish_port}{safe_path}"
        try:
            with warnings.catch_warnings():
                if config.redfish_verify is False:
                    # This suppression is scoped to this one request; TLS verification
                    # remains enabled for every other HTTP client in the process.
                    warnings.simplefilter("ignore", InsecureRequestWarning)
                response = self.redfish_get(
                    url,
                    auth=(config.username, config.password),
                    headers={"Accept": "application/json"},
                    timeout=(min(3.05, config.timeout_seconds), config.timeout_seconds),
                    verify=config.redfish_verify,
                    allow_redirects=False,
                )
        except requests.RequestException as exc:
            raise ApiError(502, "redfish_unavailable", "iDRAC Redfish 请求失败") from exc
        if response.status_code == 401:
            raise ApiError(401, "idrac_auth_failed", "iDRAC 用户名或密码错误")
        if response.status_code == 404:
            raise ApiError(404, "redfish_not_found", "Redfish 资源不存在")
        if not 200 <= response.status_code < 300:
            raise ApiError(
                502,
                "redfish_http_error",
                f"iDRAC Redfish 返回 HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(502, "redfish_invalid_json", "iDRAC Redfish 返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError(502, "redfish_invalid_json", "iDRAC Redfish 返回格式异常")
        return payload

    def collect_redfish(self, config: ConnectionConfig) -> dict[str, Any]:
        known_root = f"/redfish/v1/Chassis/{self.chassis_id}"
        thermal: dict[str, Any] | None = None
        power: dict[str, Any] | None = None
        direct_errors: list[str] = []
        for suffix, destination in (("Thermal", "thermal"), ("Power", "power")):
            try:
                payload = self.redfish_json(config, f"{known_root}/{suffix}")
                if destination == "thermal":
                    thermal = payload
                else:
                    power = payload
            except ApiError as exc:
                direct_errors.append(exc.code)

        if thermal is None or power is None:
            try:
                discovered_thermal, discovered_power = self._discover_chassis_resources(config)
                thermal = thermal or discovered_thermal
                power = power or discovered_power
            except ApiError:
                # A useful partial Redfish response is still preferable to making
                # the dashboard wait for multiple legacy IPMI calls.
                if thermal is None and power is None:
                    raise

        result = _parse_redfish_telemetry(thermal or {}, power or {})
        if not result["temperatures"] and not result["fans"] and not result["power"]:
            raise ApiError(
                502,
                "redfish_empty",
                "Redfish 未返回可用的温度、风扇或功耗数据",
                {"probes": direct_errors},
            )
        result.update({"source": "redfish", "observed_at": _utc_now()})
        if direct_errors and (thermal is None or power is None):
            result["errors"] = ["部分 Redfish 资源不可用"]
        return result

    def _discover_chassis_resources(
        self, config: ConnectionConfig
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        collection = self.redfish_json(config, "/redfish/v1/Chassis")
        members = collection.get("Members") or []
        candidates: list[str] = []
        for member in members:
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str):
                candidates.append(_normalise_redfish_path(member["@odata.id"]))
        candidates.sort(key=lambda value: "system.embedded.1" not in value.casefold())
        for chassis_path in candidates:
            try:
                chassis = self.redfish_json(config, chassis_path)
            except ApiError:
                continue
            thermal_path = _linked_path(chassis, "Thermal") or f"{chassis_path}/Thermal"
            power_path = _linked_path(chassis, "Power") or f"{chassis_path}/Power"
            thermal = power = None
            try:
                thermal = self.redfish_json(config, thermal_path)
            except ApiError:
                pass
            try:
                power = self.redfish_json(config, power_path)
            except ApiError:
                pass
            if thermal is not None or power is not None:
                return thermal, power
        raise ApiError(502, "redfish_chassis_missing", "未找到可读取的 Redfish Chassis")

    def collect_ipmi_summary(self, config: ConnectionConfig) -> dict[str, Any]:
        temperatures: list[dict[str, Any]] = []
        fans: list[dict[str, Any]] = []
        errors: list[str] = []
        for sensor_type, target in (("Temperature", temperatures), ("Fan", fans)):
            result = self.ipmi.run(config, ("sdr", "type", sensor_type), timeout=12)
            if result.ok:
                for record in _parse_sdr_records(result.stdout):
                    parsed = _record_to_summary(record)
                    if parsed:
                        target.append(parsed)
            else:
                errors.append(f"{sensor_type} 查询失败")

        power: dict[str, Any] = {}
        power_result = self.ipmi.run(config, ("dcmi", "power", "reading"), timeout=12)
        if power_result.ok:
            power = _parse_dcmi_power(power_result.stdout)
        else:
            errors.append("功耗查询失败")
        if not temperatures and not fans and not power:
            raise ApiError(502, "ipmi_telemetry_failed", "ipmitool 未返回遥测数据")
        return {
            "source": "ipmitool",
            "observed_at": _utc_now(),
            "temperatures": temperatures,
            "fans": fans,
            "power": power,
            "alerts": _alerts_from_records([*temperatures, *fans]),
            "errors": errors,
        }

    def collect_telemetry(self, config: ConnectionConfig) -> dict[str, Any]:
        try:
            result = self.collect_redfish(config)
        except ApiError as redfish_error:
            result = self.collect_ipmi_summary(config)
            result["errors"] = [
                "Redfish 不可用，已回退 ipmitool",
                *result.get("errors", []),
            ]
            result["fallback_reason"] = redfish_error.code
            return result
        if result["temperatures"] and result["fans"] and result["power"]:
            return result
        # Some iDRAC8 firmware exposes only part of Chassis Thermal/Power.
        # Fill just the missing dashboard groups through the faster typed IPMI
        # queries; the expensive all-SDR walk remains manual-only.
        try:
            fallback = self.collect_ipmi_summary(config)
        except ApiError:
            return result
        used_fallback = False
        for key in ("temperatures", "fans", "power"):
            if not result[key] and fallback[key]:
                result[key] = fallback[key]
                used_fallback = True
        if used_fallback:
            result["source"] = "redfish+ipmitool"
            result["alerts"] = _alerts_from_records(
                [*result["temperatures"], *result["fans"]]
            )
            result["errors"] = [
                *result.get("errors", []),
                "部分 Redfish 遥测缺失，已用 ipmitool 补齐",
            ]
        return result

    def request_telemetry(self) -> tuple[dict[str, Any], int]:
        now = time.monotonic()
        with self.state.lock:
            age = now - self.state.telemetry_time if self.state.telemetry else None
            fresh = age is not None and age < self.state.cache_ttl
            if fresh:
                return {
                    "telemetry": self.state.telemetry,
                    "age_seconds": round(age or 0.0, 2),
                    "stale": False,
                    "refreshing": False,
                    "error": self.state.telemetry_error,
                }, 200
            retry_ready = (
                not self.state.telemetry_attempt_time
                or now - self.state.telemetry_attempt_time >= self.state.cache_ttl
            )
            if not self.state.telemetry_refreshing and retry_ready:
                config, revision = self.state.config, self.state.config_revision
                _require_connection(config)
                self.state.telemetry_refreshing = True
                self.state.telemetry_attempt_time = now
                threading.Thread(
                    target=self._refresh_telemetry,
                    args=(config, revision),
                    name="idrac-telemetry-refresh",
                    daemon=True,
                ).start()
            data = {
                "telemetry": self.state.telemetry,
                "age_seconds": round(age, 2) if age is not None else None,
                "stale": True,
                "refreshing": self.state.telemetry_refreshing,
                "error": self.state.telemetry_error,
            }
            return data, 200 if self.state.telemetry is not None else 202

    def _refresh_telemetry(self, config: ConnectionConfig, revision: int) -> None:
        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            # Verify identity immediately before the first credential-bearing
            # request.  Failure is closed: collect_telemetry is never entered.
            config, revision = self.refresh_endpoint(require_verified=True)
            result = self.collect_telemetry(config)
        except Exception as exc:  # Background tasks must always reset the refresh flag.
            error = _safe_exception(exc, config.password)
        sample: dict[str, Any] | None = None
        with self.state.lock:
            if revision == self.state.config_revision:
                if result is not None:
                    self.state.telemetry = result
                    self.state.telemetry_time = time.monotonic()
                    sample = _history_sample_from_telemetry(result)
                    self.state.history.append(sample)
                self.state.telemetry_error = error
            self.state.telemetry_refreshing = False
        # Persist outside the state lock: flushing can touch the disk, and the
        # dashboard must never block behind it.
        if sample is not None and self.state.store is not None:
            self.state.store.record(sample)

    def start_deep_scan(self) -> dict[str, Any]:
        config, revision = self.refresh_endpoint(require_verified=True)
        _require_connection(config)
        with self.state.lock:
            if self.state.deep_scan.get("status") == "running":
                return dict(self.state.deep_scan)
            job_id = secrets.token_urlsafe(12)
            job = {
                "job_id": job_id,
                "status": "running",
                "started_at": _utc_now(),
            }
            self.state.deep_scan = job
        threading.Thread(
            target=self._run_deep_scan,
            args=(config, revision, job_id),
            name="idrac-deep-scan",
            daemon=True,
        ).start()
        return dict(job)

    def _run_deep_scan(
        self, config: ConnectionConfig, revision: int, job_id: str
    ) -> None:
        completed: dict[str, Any]
        try:
            result = self.ipmi.run(config, ("sdr", "elist", "all"), timeout=60)
            records = _parse_sdr_records(result.stdout)
            # ipmitool 1.8.19 segfaults partway through the SDR walk on this
            # iDRAC8 (firmware 2.70): it prints most records, then dies with
            # SIGSEGV. Everything it printed before dying is real sensor data,
            # and discarding it would both hide usable readings and invite the
            # operator to retry — and each crashed run leaks an IPMI session
            # until the BMC's session table is exhausted. So keep what arrived
            # and label it, instead of failing and being retried.
            partial = result.returncode in SIGSEGV_RETURNCODES and bool(records)
            if not result.ok and not partial:
                raise ApiError(502, "deep_scan_failed", "完整传感器扫描失败")
            completed = {
                "job_id": job_id,
                "status": "complete",
                "finished_at": _utc_now(),
                "result": {
                    "records": records,
                    "summary": _summarise_records(records),
                    "elapsed_seconds": result.elapsed_seconds,
                    "partial": partial,
                    "partial_reason": "ipmitool_sigsegv" if partial else None,
                },
            }
        except Exception as exc:
            completed = {
                "job_id": job_id,
                "status": "error",
                "finished_at": _utc_now(),
                "error": _safe_exception(exc, config.password),
            }
        with self.state.lock:
            revision_match = revision == self.state.config_revision
            if revision_match and self.state.deep_scan.get("job_id") == job_id:
                self.state.deep_scan = completed


def _redact_and_limit(value: str, password: str) -> str:
    if password:
        value = value.replace(password, "[REDACTED]")
    return value[:SAFE_OUTPUT_LIMIT].strip()


def _safe_exception(exc: Exception, password: str) -> str:
    if isinstance(exc, ApiError):
        return exc.message
    return _redact_and_limit(str(exc), password)[:300] or "未知错误"


def _require_connection(config: ConnectionConfig) -> None:
    if not config.host or not config.username or not config.password:
        raise ApiError(400, "connection_not_configured", "请先填写 iDRAC 连接信息")


def _validate_host(value: Any) -> str:
    host = str(value or "").strip()
    if not HOST_PATTERN.fullmatch(host) or host.startswith("-"):
        raise ApiError(400, "invalid_host", "iDRAC 地址必须是 IPv4 地址或主机名")
    return host


def _validate_username(value: Any) -> str:
    username = str(value or "").strip()
    if not username or len(username) > 64 or any(ord(char) < 32 for char in username):
        raise ApiError(400, "invalid_username", "iDRAC 用户名无效")
    return username


def _validate_port(value: Any, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "invalid_port", f"{field} 端口无效") from exc
    if not 1 <= port <= 65535:
        raise ApiError(400, "invalid_port", f"{field} 端口必须为 1-65535")
    return port


def _normalise_redfish_path(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme or parsed.netloc else value.split("?", 1)[0]
    if not path.startswith("/redfish/") or ".." in path:
        raise ApiError(502, "redfish_invalid_link", "iDRAC 返回了无效的 Redfish 链接")
    return path.rstrip("/") or "/redfish/v1"


def _linked_path(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
        return _normalise_redfish_path(value["@odata.id"])
    return None


def _status_text(item: Mapping[str, Any]) -> str:
    status = item.get("Status")
    if not isinstance(status, dict):
        return "unknown"
    return str(status.get("Health") or status.get("State") or "unknown")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return None


def _history_sample_from_telemetry(
    telemetry: Mapping[str, Any],
) -> dict[str, Any]:
    temperatures = telemetry.get("temperatures")
    temperature_values = [
        value
        for item in temperatures if isinstance(item, Mapping)
        for value in [_number(item.get("celsius"))]
        if value is not None
    ] if isinstance(temperatures, list) else []

    fans = telemetry.get("fans")
    fan_values = [
        value
        for item in fans if isinstance(item, Mapping)
        for value in [_number(item.get("rpm"))]
        if value is not None
    ] if isinstance(fans, list) else []

    power = telemetry.get("power")
    power_watts = (
        _number(power.get("consumed_watts")) if isinstance(power, Mapping) else None
    )
    return {
        "timestamp": str(telemetry.get("observed_at") or _utc_now())[:80],
        "max_temp_c": max(temperature_values) if temperature_values else None,
        "avg_fan_rpm": (
            round(sum(fan_values) / len(fan_values), 2) if fan_values else None
        ),
        "power_watts": power_watts,
        "source": str(telemetry.get("source") or "unknown")[:64],
    }


def _public_telemetry_value(value: Any) -> dict[str, Any]:
    """Build an explicit anonymous response; new backend fields stay private."""

    if not isinstance(value, Mapping):
        return {
            "telemetry": None,
            "age_seconds": None,
            "stale": True,
            "refreshing": False,
        }

    public: dict[str, Any] = {
        "telemetry": None,
        "age_seconds": _number(value.get("age_seconds")),
        "stale": bool(value.get("stale")),
        "refreshing": bool(value.get("refreshing")),
    }
    telemetry = value.get("telemetry")
    if not isinstance(telemetry, Mapping):
        return public

    temperatures: list[dict[str, Any]] = []
    for item in telemetry.get("temperatures") or []:
        if not isinstance(item, Mapping):
            continue
        temperatures.append(
            {
                "name": str(item.get("name") or "Temperature")[:160],
                "celsius": _number(item.get("celsius")),
                "status": str(item.get("status") or "unknown")[:64],
                "upper_warning": _number(item.get("upper_warning")),
                "upper_critical": _number(item.get("upper_critical")),
            }
        )

    fans: list[dict[str, Any]] = []
    for item in telemetry.get("fans") or []:
        if not isinstance(item, Mapping):
            continue
        fans.append(
            {
                "name": str(item.get("name") or "Fan")[:160],
                "rpm": _number(item.get("rpm")),
                "percent": _number(item.get("percent")),
                "status": str(item.get("status") or "unknown")[:64],
            }
        )

    power_value = telemetry.get("power")
    power = {
        key: number
        for key in (
            "consumed_watts",
            "capacity_watts",
            "allocated_watts",
            "average_watts",
            "minimum_watts",
            "maximum_watts",
        )
        if isinstance(power_value, Mapping)
        and (number := _number(power_value.get(key))) is not None
    }
    alerts = [
        {
            "name": str(item.get("name") or "Sensor")[:160],
            "status": str(item.get("status") or "unknown")[:64],
        }
        for item in (telemetry.get("alerts") or [])
        if isinstance(item, Mapping)
    ]
    public["telemetry"] = {
        "source": str(telemetry.get("source") or "unknown")[:64],
        "observed_at": str(telemetry.get("observed_at") or "")[:80],
        "temperatures": temperatures,
        "fans": fans,
        "power": power,
        "alerts": alerts,
    }
    return public


def _parse_redfish_telemetry(
    thermal: Mapping[str, Any], power_payload: Mapping[str, Any]
) -> dict[str, Any]:
    temperatures: list[dict[str, Any]] = []
    fans: list[dict[str, Any]] = []
    for item in thermal.get("Temperatures") or []:
        if not isinstance(item, dict):
            continue
        temperatures.append(
            {
                "name": str(item.get("Name") or item.get("MemberId") or "Temperature"),
                "celsius": _number(item.get("ReadingCelsius")),
                "status": _status_text(item),
                "upper_warning": _number(item.get("UpperThresholdNonCritical")),
                "upper_critical": _number(item.get("UpperThresholdCritical")),
            }
        )
    for item in thermal.get("Fans") or []:
        if not isinstance(item, dict):
            continue
        reading = _number(item.get("Reading"))
        units = str(item.get("ReadingUnits") or "").casefold()
        fans.append(
            {
                "name": str(item.get("Name") or item.get("MemberId") or "Fan"),
                "rpm": reading if units in {"rpm", "revolutionsperminute"} else None,
                "percent": reading if "percent" in units else None,
                "status": _status_text(item),
            }
        )

    power: dict[str, Any] = {}
    controls = power_payload.get("PowerControl") or []
    if controls and isinstance(controls[0], dict):
        control = controls[0]
        power = {
            "consumed_watts": _number(control.get("PowerConsumedWatts")),
            "capacity_watts": _number(control.get("PowerCapacityWatts")),
            "allocated_watts": _number(control.get("PowerAllocatedWatts")),
        }
        metrics = control.get("PowerMetrics")
        if isinstance(metrics, dict):
            power.update(
                {
                    "average_watts": _number(metrics.get("AverageConsumedWatts")),
                    "minimum_watts": _number(metrics.get("MinConsumedWatts")),
                    "maximum_watts": _number(metrics.get("MaxConsumedWatts")),
                }
            )
        power = {key: value for key, value in power.items() if value is not None}
    alerts = _alerts_from_records([*temperatures, *fans])
    return {
        "temperatures": temperatures,
        "fans": fans,
        "power": power,
        "alerts": alerts,
        "errors": [],
    }


def _sensor_category(name: str, reading: str) -> str:
    searchable = f"{name} {reading}".casefold()
    if "degrees c" in searchable or "temp" in searchable:
        return "temperature"
    if "rpm" in searchable or "fan" in searchable:
        return "fan"
    if "watt" in searchable or "power" in searchable:
        return "power"
    if "volt" in searchable:
        return "voltage"
    if "amp" in searchable or "current" in searchable:
        return "current"
    return "system"


def _parse_sdr_records(output: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [part.strip() for part in line.split("|")]
        parsed = len(fields) >= 5
        if parsed:
            name, sensor_id, status, entity = fields[:4]
            reading = " | ".join(fields[4:])
        else:
            name, sensor_id, status, entity, reading = line, "", "raw", "", ""
        records.append(
            {
                "name": name or "Unnamed sensor",
                "sensor_id": sensor_id,
                "status": status,
                "entity": entity,
                "reading": reading,
                "category": _sensor_category(name, reading),
                "parsed": parsed,
                "raw": line,
            }
        )
    return records


def _extract_reading_number(reading: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", reading)
    return round(float(match.group(0)), 2) if match else None


def _record_to_summary(record: Mapping[str, Any]) -> dict[str, Any] | None:
    category = record.get("category")
    if category == "temperature":
        return {
            "name": record["name"],
            "celsius": _extract_reading_number(str(record.get("reading", ""))),
            "status": record.get("status", "unknown"),
        }
    if category == "fan":
        return {
            "name": record["name"],
            "rpm": _extract_reading_number(str(record.get("reading", ""))),
            "percent": None,
            "status": record.get("status", "unknown"),
        }
    return None


def _parse_dcmi_power(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    patterns = {
        "consumed_watts": r"Instantaneous power reading\s*:\s*([\d.]+)\s*Watts",
        "average_watts": r"Average power reading[^:]*:\s*([\d.]+)\s*Watts",
        "minimum_watts": r"Minimum power over sample duration\s*:\s*([\d.]+)\s*Watts",
        "maximum_watts": r"Maximum power over sample duration\s*:\s*([\d.]+)\s*Watts",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            result[key] = round(float(match.group(1)), 2)
    return result


def _alerts_from_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    normal = {"", "ok", "enabled", "unknown", "na", "ns"}
    alerts: list[dict[str, str]] = []
    for record in records:
        status = str(record.get("status") or "unknown")
        if status.casefold() not in normal:
            alerts.append({"name": str(record.get("name") or "Sensor"), "status": status})
    return alerts


def _summarise_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    categories: defaultdict[str, int] = defaultdict(int)
    for record in records:
        categories[str(record.get("category") or "system")] += 1
    return {
        "total": len(records),
        "categories": dict(sorted(categories.items())),
        "alerts": _alerts_from_records(records),
    }


def _parse_key_value_output(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


class _FingerprintAdapter(requests.adapters.HTTPAdapter):
    """Pin the iDRAC's TLS certificate by SHA-256 fingerprint.

    An iDRAC ships a self-signed certificate, so ordinary CA validation cannot
    be turned on without building a CA. Without *some* server identity check,
    Redfish HTTP Basic auth hands the iDRAC root password to whatever answers
    on port 443 — an on-path attacker between the container and the BMC gets
    the credential. Pinning the leaf certificate closes that without needing
    any PKI: urllib3 asserts the fingerprint during the handshake and aborts
    the connection before the Authorization header is ever sent.
    """

    def __init__(self, fingerprint: str, **kwargs: Any) -> None:
        self._fingerprint = fingerprint
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any):
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args: Any, **kwargs: Any):
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().proxy_manager_for(*args, **kwargs)


def _normalise_fingerprint(value: str) -> str:
    """Accept `AA:BB:...` or bare hex; reject anything that is not SHA-256."""

    candidate = re.sub(r"[\s:-]", "", str(value or "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise RuntimeError(
            "REDFISH_TLS_FINGERPRINT must be a SHA-256 certificate fingerprint "
            "(64 hex characters, colons optional)"
        )
    return candidate


def _redfish_client_from_environment() -> Callable[..., Any]:
    fingerprint = os.getenv("REDFISH_TLS_FINGERPRINT", "").strip()
    if not fingerprint:
        return requests.get
    session = requests.Session()
    session.mount("https://", _FingerprintAdapter(_normalise_fingerprint(fingerprint)))
    return session.get


def _tls_setting_from_environment() -> bool | str:
    raw = os.getenv("REDFISH_VERIFY_TLS", "false").strip()
    if raw.casefold() in {"false", "0", "no", "off"}:
        return False
    if raw.casefold() in {"true", "1", "yes", "on"}:
        return True
    return raw


def _idrac_password_from_environment() -> str:
    secret_file = os.getenv("IDRAC_PASSWORD_FILE", "").strip()
    if secret_file:
        try:
            with Path(secret_file).open("r", encoding="utf-8") as handle:
                password = handle.read(258).rstrip("\r\n")
                has_more = bool(handle.read(1))
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("Unable to read IDRAC_PASSWORD_FILE") from exc
        if has_more or len(password) > 256:
            raise RuntimeError("IDRAC_PASSWORD_FILE contains an invalid secret")
        return password

    password = os.getenv("IDRAC_PASSWORD", os.getenv("IPMI_PASSWORD", ""))
    if len(password) > 256:
        raise RuntimeError("IDRAC_PASSWORD is too long")
    return password


def _initial_connection() -> ConnectionConfig:
    return ConnectionConfig(
        host=_validate_host(os.getenv("IDRAC_HOST", "192.168.5.151")),
        username=_validate_username(os.getenv("IDRAC_USER", "root")),
        password=_idrac_password_from_environment(),
        ipmi_port=_validate_port(os.getenv("IDRAC_IPMI_PORT", "623"), "IPMI"),
        redfish_port=_validate_port(os.getenv("IDRAC_REDFISH_PORT", "443"), "Redfish"),
        redfish_verify=_tls_setting_from_environment(),
        timeout_seconds=float(os.getenv("IDRAC_TIMEOUT_SECONDS", "10")),
    )


def _mac_discovery_from_environment() -> MacAddressDiscovery | None:
    mac = os.getenv("IDRAC_MAC", "").strip()
    if not mac:
        return None
    try:
        return MacAddressDiscovery(
            mac,
            os.getenv("IDRAC_ARP_FILE", "/run/host-proc-net-arp"),
            network=os.getenv("IDRAC_DISCOVERY_CIDR", ""),
            interface=os.getenv("IDRAC_ARP_INTERFACE", ""),
            scan_interval=float(os.getenv("IDRAC_DISCOVERY_SCAN_INTERVAL", "60")),
            probe_timeout=float(os.getenv("IDRAC_DISCOVERY_PROBE_TIMEOUT", "0.6")),
            max_hosts=int(os.getenv("IDRAC_DISCOVERY_MAX_HOSTS", "256")),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid iDRAC MAC discovery configuration: {exc}") from exc


def _request_json() -> dict[str, Any]:
    if not request.is_json:
        raise ApiError(415, "json_required", "请求必须使用 application/json")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_json", "JSON 请求体格式无效")
    return payload


def create_app(
    test_config: Mapping[str, Any] | None = None,
    *,
    ipmi_runner: Any | None = None,
    redfish_get: Callable[..., Any] | None = None,
    mac_discovery: MacAddressDiscovery | None = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=64 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=_env_bool("WEB_COOKIE_SECURE", False),
        SESSION_COOKIE_NAME="r730xd_session",
        PERMANENT_SESSION_LIFETIME=1800,
        AUTH_MODE=os.getenv("AUTH_MODE", "idrac"),
        WEB_USERNAME=os.getenv("WEB_USERNAME", "admin"),
        WEB_PASSWORD=os.getenv("WEB_PASSWORD", ""),
        WEB_PASSWORD_HASH=os.getenv("WEB_PASSWORD_HASH", ""),
        TRUSTED_ORIGINS=tuple(
            item.strip().rstrip("/")
            for item in os.getenv("TRUSTED_ORIGINS", "").split(",")
            if item.strip()
        ),
        REQUIRE_ORIGIN=_env_bool("REQUIRE_ORIGIN", True),
        TELEMETRY_CACHE_TTL=float(os.getenv("TELEMETRY_CACHE_TTL", "8")),
        TELEMETRY_SAMPLE_INTERVAL=float(
            os.getenv("TELEMETRY_SAMPLE_INTERVAL", "15")
        ),
        HISTORY_MAX_SAMPLES=int(os.getenv("HISTORY_MAX_SAMPLES", "90")),
        # Empty disables persistence and keeps the pre-2026-08 memory-only
        # behaviour, which is what the unit tests and a volume-less run get.
        TELEMETRY_DB_PATH=os.getenv("TELEMETRY_DB_PATH", ""),
        TELEMETRY_RETENTION_DAYS=int(os.getenv("TELEMETRY_RETENTION_DAYS", "30")),
        TELEMETRY_FLUSH_INTERVAL=float(os.getenv("TELEMETRY_FLUSH_INTERVAL", "60")),
        TELEMETRY_FLUSH_THRESHOLD=int(os.getenv("TELEMETRY_FLUSH_THRESHOLD", "20")),
        # Floor on how often an anonymous visitor may start an SDR walk.
        DEEP_SCAN_MIN_INTERVAL=float(os.getenv("DEEP_SCAN_MIN_INTERVAL", "60")),
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    session_secret = str(app.config.get("SECRET_KEY") or "")
    if not app.config["TESTING"] and (
        len(session_secret.encode("utf-8")) < 32
        or session_secret.casefold().startswith("replace-with")
    ):
        raise RuntimeError(
            "FLASK_SECRET_KEY must be a non-placeholder secret of at least 32 bytes"
        )

    auth_mode = str(app.config.get("AUTH_MODE") or "static").strip().casefold()
    if auth_mode not in {"static", "idrac"}:
        raise RuntimeError("AUTH_MODE must be either 'static' or 'idrac'")
    app.config["AUTH_MODE"] = auth_mode

    store: TelemetryStore | None = None
    database_path = str(app.config.get("TELEMETRY_DB_PATH") or "").strip()
    if database_path:
        store = TelemetryStore(
            database_path,
            retention_days=int(app.config["TELEMETRY_RETENTION_DAYS"]),
            flush_interval=float(app.config["TELEMETRY_FLUSH_INTERVAL"]),
            flush_threshold=int(app.config["TELEMETRY_FLUSH_THRESHOLD"]),
        )
        app.extensions["telemetry_store"] = store

    state = RuntimeState(
        _initial_connection(),
        float(app.config["TELEMETRY_CACHE_TTL"]),
        int(app.config["HISTORY_MAX_SAMPLES"]),
        store=store,
    )
    backend = Backend(
        state,
        ipmi_runner
        or IpmiRunner(
            os.getenv("IPMITOOL_PATH")
            or shutil.which("ipmitool")
            or "/usr/bin/ipmitool"
        ),
        redfish_get or _redfish_client_from_environment(),
        os.getenv("REDFISH_CHASSIS_ID", "System.Embedded.1"),
        mac_discovery if mac_discovery is not None else _mac_discovery_from_environment(),
    )
    app.extensions["r730xd_backend"] = backend
    app.extensions["login_limiter"] = LoginLimiter()

    def login_required(view: Callable[..., Any]):
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            if not session.get("authenticated"):
                raise ApiError(401, "login_required", "请先登录 Web 控制台")
            return view(*args, **kwargs)

        return wrapped

    def enforce_same_origin() -> None:
        if request.headers.get("Sec-Fetch-Site", "").casefold() == "cross-site":
            raise ApiError(403, "cross_site_request", "拒绝跨站请求")
        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")
        supplied = origin or referer
        if not supplied:
            if app.config["REQUIRE_ORIGIN"] and not app.config["TESTING"]:
                raise ApiError(403, "origin_required", "请求缺少同源标识")
            return
        parsed = urlparse(supplied)
        candidate = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        allowed = {
            request.host_url.rstrip("/"),
            *app.config.get("TRUSTED_ORIGINS", ()),
        }
        if candidate not in allowed:
            raise ApiError(403, "origin_mismatch", "拒绝非同源请求")

    @app.before_request
    def protect_unsafe_requests():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        enforce_same_origin()
        if request.endpoint == "login":
            return None
        if session.get("authenticated"):
            expected = str(session.get("csrf_token") or "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                raise ApiError(403, "csrf_failed", "CSRF 校验失败，请刷新页面后重试")
        return None

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        payload: dict[str, Any] = {
            "ok": False,
            "error": {"code": error.code, "message": error.message},
        }
        if error.details:
            payload["error"]["details"] = error.details
        return jsonify(payload), error.status

    @app.errorhandler(404)
    def handle_not_found(_error):
        return jsonify(
            {"ok": False, "error": {"code": "not_found", "message": "接口不存在"}}
        ), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_error):
        return jsonify(
            {
                "ok": False,
                "error": {"code": "method_not_allowed", "message": "请求方法不允许"},
            }
        ), 405

    @app.errorhandler(413)
    def handle_too_large(_error):
        return jsonify(
            {
                "ok": False,
                "error": {"code": "request_too_large", "message": "请求体过大"},
            }
        ), 413

    @app.errorhandler(Exception)
    def handle_unexpected(_error):
        return jsonify(
            {
                "ok": False,
                "error": {"code": "internal_error", "message": "服务器内部错误"},
            }
        ), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/login")
    def login_page():
        if session.get("authenticated"):
            return redirect("/", code=302)
        return render_template("login.html")

    @app.get("/healthz")
    def healthz():
        return _json_ok({"status": "healthy"})

    @app.get("/api/auth/session")
    def auth_session():
        authenticated = bool(session.get("authenticated"))
        data: dict[str, Any] = {"authenticated": authenticated}
        if authenticated:
            data.update(
                {
                    "username": session.get("username"),
                    "csrf_token": session.get("csrf_token"),
                }
            )
        return _json_ok(data)

    @app.post("/api/auth/login")
    def login():
        limiter: LoginLimiter = app.extensions["login_limiter"]
        remote_key = _login_rate_key(request.remote_addr)
        if limiter.blocked(remote_key):
            raise ApiError(429, "login_rate_limited", "登录失败次数过多，请稍后重试")
        payload = _request_json()
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if app.config["AUTH_MODE"] == "idrac":
            try:
                idrac_username = _validate_username(username)
            except ApiError:
                idrac_username = ""
            if not idrac_username or not password or len(password) > 256:
                limiter.failure(remote_key)
                raise ApiError(401, "invalid_login", "用户名或密码错误")

            current, _revision = state.connection_snapshot()
            if current.configured:
                # A bad Web login must not consume iDRAC's very small remote
                # password-failure budget. When startup credentials are present,
                # authenticate locally using constant-time comparisons.
                user_ok = hmac.compare_digest(
                    idrac_username.encode(), current.username.encode()
                )
                password_ok = hmac.compare_digest(
                    password.encode(), current.password.encode()
                )
                if not (user_ok and password_ok):
                    limiter.failure(remote_key)
                    raise ApiError(401, "invalid_login", "用户名或密码错误")
                authenticated_username = current.username
            else:
                current, _revision = backend.refresh_endpoint(require_verified=True)
                authenticated_config = ConnectionConfig(
                    host=current.host,
                    username=idrac_username,
                    password=password,
                    ipmi_port=current.ipmi_port,
                    redfish_port=current.redfish_port,
                    redfish_verify=current.redfish_verify,
                    timeout_seconds=current.timeout_seconds,
                )
                try:
                    backend.redfish_json(
                        authenticated_config, "/redfish/v1/Managers"
                    )
                except ApiError as exc:
                    if exc.status == 401:
                        limiter.failure(remote_key)
                        raise ApiError(
                            401, "invalid_login", "用户名或密码错误"
                        ) from None
                    raise
                # Keep the verified iDRAC secret only in the process-local runtime
                # connection object. It is never copied into the signed Web session.
                state.replace_config(authenticated_config)
                authenticated_username = idrac_username
        else:
            expected_user = str(app.config["WEB_USERNAME"])
            password_hash = str(app.config.get("WEB_PASSWORD_HASH") or "")
            plain_password = str(app.config.get("WEB_PASSWORD") or "")
            if not password_hash and not plain_password:
                raise ApiError(
                    503,
                    "web_password_not_configured",
                    "服务端尚未设置 WEB_PASSWORD 或 WEB_PASSWORD_HASH",
                )
            user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
            if password_hash:
                try:
                    password_ok = check_password_hash(password_hash, password)
                except ValueError:
                    password_ok = False
            else:
                password_ok = hmac.compare_digest(
                    password.encode(), plain_password.encode()
                )
            if not (user_ok and password_ok):
                limiter.failure(remote_key)
                raise ApiError(401, "invalid_login", "用户名或密码错误")
            authenticated_username = expected_user

        limiter.success(remote_key)
        session.clear()
        session.permanent = True
        session["authenticated"] = True
        session["username"] = authenticated_username
        session["csrf_token"] = secrets.token_urlsafe(32)
        return _json_ok(
            {
                "authenticated": True,
                "username": authenticated_username,
                "csrf_token": session["csrf_token"],
            }
        )

    @app.post("/api/auth/logout")
    @login_required
    def logout():
        session.clear()
        return _json_ok({"authenticated": False})

    @app.get("/api/config")
    @login_required
    def get_config():
        config, _revision = state.connection_snapshot()
        return _json_ok({"connection": config.public_dict()})

    @app.put("/api/config")
    @login_required
    def update_config():
        payload = _request_json()
        current, _revision = state.connection_snapshot()
        password_value = payload.get("password")
        if password_value is not None and not isinstance(password_value, str):
            raise ApiError(400, "invalid_password", "iDRAC 密码必须为字符串")
        password = (
            current.password
            if password_value is None or password_value == ""
            else str(password_value)
        )
        if len(password) > 256:
            raise ApiError(400, "invalid_password", "iDRAC 密码长度无效")
        verify_value: bool | str = current.redfish_verify
        if "redfish_verify_tls" in payload:
            if not isinstance(payload["redfish_verify_tls"], bool):
                raise ApiError(400, "invalid_tls_setting", "TLS 验证设置必须为布尔值")
            verify_value = payload["redfish_verify_tls"]
        new_config = ConnectionConfig(
            host=_validate_host(payload.get("host", current.host)),
            username=_validate_username(payload.get("username", current.username)),
            password=password,
            ipmi_port=_validate_port(payload.get("ipmi_port", current.ipmi_port), "IPMI"),
            redfish_port=_validate_port(
                payload.get("redfish_port", current.redfish_port), "Redfish"
            ),
            redfish_verify=verify_value,
            timeout_seconds=current.timeout_seconds,
        )
        state.replace_config(new_config)
        return _json_ok({"connection": new_config.public_dict()})

    @app.get("/api/status")
    def status():
        config, _revision = state.connection_snapshot()
        with state.lock:
            age = (
                round(time.monotonic() - state.telemetry_time, 2)
                if state.telemetry is not None
                else None
            )
            telemetry_status = {
                "available": state.telemetry is not None,
                "age_seconds": age,
                "refreshing": state.telemetry_refreshing,
            }
            if session.get("authenticated"):
                telemetry_status["error"] = state.telemetry_error
                telemetry_status["persistence"] = (
                    store.stats() if store is not None else {"enabled": False}
                )
        connection = (
            config.public_dict()
            if session.get("authenticated")
            else {"configured": config.configured}
        )
        return _json_ok(
            {
                "connection": connection,
                "control": state.control_public(),
                "telemetry": telemetry_status,
            }
        )

    @app.post("/api/connection/test")
    @login_required
    def test_connection():
        config, _revision = backend.refresh_endpoint(require_verified=True)
        result = backend.ipmi.run(config, ("mc", "info"), timeout=12)
        if not result.ok:
            raise ApiError(
                502,
                "connection_failed",
                "无法连接 iDRAC",
                {"stderr": result.stderr[:500]},
            )
        return _json_ok(
            {
                "connected": True,
                "elapsed_seconds": result.elapsed_seconds,
                "device": _parse_key_value_output(result.stdout),
            }
        )

    @app.post("/api/control/interlock")
    @login_required
    def set_interlock():
        payload = _request_json()
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(400, "invalid_interlock", "enabled 必须为布尔值")
        with state.lock:
            state.safety_unlocked = enabled
        return _json_ok({"control": state.control_public()})

    @app.post("/api/control/manual")
    @login_required
    def enable_manual():
        payload = _request_json()
        if payload.get("confirmed") is not True:
            raise ApiError(400, "confirmation_required", "必须明确确认启用手动温控")
        with state.control_lock:
            backend.refresh_endpoint(require_verified=True)
            with state.lock:
                if not state.safety_unlocked:
                    raise ApiError(409, "interlock_locked", "请先解除安全联锁")
                config = state.config
            result = backend.ipmi.run(config, MANUAL_MODE_RAW, timeout=12)
            if not result.ok:
                raise ApiError(502, "manual_mode_failed", "启用手动温控失败")
            with state.lock:
                state.mode = "manual"
        return _json_ok({"control": state.control_public()})

    @app.post("/api/control/auto")
    @login_required
    def restore_auto():
        with state.control_lock:
            backend.refresh_endpoint(require_verified=True)
            config, _revision = state.connection_snapshot()
            result = backend.ipmi.run(config, AUTO_MODE_RAW, timeout=12)
            if not result.ok:
                raise ApiError(502, "auto_mode_failed", "恢复自动温控失败")
            with state.lock:
                state.mode = "auto"
                state.safety_unlocked = False
                state.percent = None
        return _json_ok({"control": state.control_public()})

    @app.post("/api/control/speed")
    @login_required
    def set_speed():
        payload = _request_json()
        percent_value = payload.get("percent")
        if isinstance(percent_value, bool) or not isinstance(percent_value, int):
            raise ApiError(400, "invalid_speed", "风扇百分比必须为 5-100 的整数")
        percent = percent_value
        if not 5 <= percent <= 100:
            raise ApiError(400, "invalid_speed", "风扇百分比必须为 5-100 的整数")
        with state.control_lock:
            backend.refresh_endpoint(require_verified=True)
            with state.lock:
                if not state.safety_unlocked:
                    raise ApiError(409, "interlock_locked", "安全联锁尚未解除")
                if state.mode != "manual":
                    raise ApiError(409, "manual_mode_required", "必须先成功启用手动温控")
                config = state.config
            arguments = ("raw", "0x30", "0x30", "0x02", "0xff", f"0x{percent:02x}")
            result = backend.ipmi.run(config, arguments, timeout=12)
            if not result.ok:
                raise ApiError(502, "speed_change_failed", "设置风扇转速失败")
            with state.lock:
                state.percent = percent
        return _json_ok({"control": state.control_public()})

    @app.get("/api/telemetry/summary")
    def telemetry_summary():
        if session.get("authenticated") and request.args.get(
            "refresh", ""
        ).casefold() in {"1", "true", "yes"}:
            with state.lock:
                state.telemetry_time = 0.0
                state.telemetry_attempt_time = 0.0
        data, response_status = backend.request_telemetry()
        return _json_ok(_public_telemetry_value(data), response_status)

    HISTORY_RANGES = {
        "5m": 300,
        "1h": 3600,
        "6h": 21600,
        "24h": 86400,
        "7d": 604800,
    }

    @app.get("/api/telemetry/history")
    def telemetry_history():
        with state.lock:
            memory_samples = [dict(sample) for sample in state.history]

        requested = request.args.get("range", "").strip().casefold()
        samples = memory_samples
        source = "memory"
        if requested:
            if requested not in HISTORY_RANGES:
                raise ApiError(
                    400,
                    "invalid_range",
                    f"range 必须是 {'、'.join(HISTORY_RANGES)} 之一",
                )
            window = HISTORY_RANGES[requested]
            cutoff = (
                datetime.now(UTC) - timedelta(seconds=window)
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
            recent_memory = [
                sample
                for sample in memory_samples
                if str(sample.get("timestamp") or "") >= cutoff
            ]
            stored = (
                store.samples(window) if store is not None and store.enabled else []
            )
            if stored:
                # Union rather than either/or: right after a restart the deque
                # holds samples the database has not been given yet, and for
                # long ranges the database holds far more than the deque can.
                merged = {
                    str(sample["timestamp"]): sample
                    for sample in (*stored, *recent_memory)
                    if sample.get("timestamp")
                }
                samples = [merged[key] for key in sorted(merged)]
                source = "sqlite+memory" if len(samples) > len(stored) else "sqlite"
            else:
                samples = recent_memory

        # current/previous stay on the live deque: they drive the "last three
        # refreshes" strip, which must not show a downsampled bucket average.
        return _json_ok(
            {
                "current": memory_samples[-1] if memory_samples else None,
                "previous": memory_samples[-2] if len(memory_samples) >= 2 else None,
                "previous2": memory_samples[-3] if len(memory_samples) >= 3 else None,
                "samples": samples,
                "range": requested or None,
                "source": source,
                "persistence": {
                    "enabled": bool(store is not None and store.enabled),
                    "ranges": sorted(HISTORY_RANGES, key=HISTORY_RANGES.get),
                },
            }
        )

    @app.post("/api/sensors/deep-scan")
    def start_deep_scan():
        # Anonymous visitors may trigger a scan, but `sdr elist all` is a heavy
        # read against a resource-constrained iDRAC8. Without a floor on the
        # interval, anyone on the LAN could keep the BMC permanently busy, so
        # requests inside the cooldown return the last result instead of
        # starting another walk. Authenticated operators bypass the cooldown.
        cooldown = float(app.config["DEEP_SCAN_MIN_INTERVAL"])
        if not session.get("authenticated"):
            now = time.monotonic()
            with state.lock:
                running = state.deep_scan.get("status") == "running"
                started = state.deep_scan_started
                job = dict(state.deep_scan)
            since = now - started
            if running:
                return _json_ok(job, 202)
            if started and since < cooldown:
                job["throttled"] = True
                job["retry_after_seconds"] = round(cooldown - since, 1)
                return _json_ok(job, 200)
        with state.lock:
            state.deep_scan_started = time.monotonic()
        job = backend.start_deep_scan()
        return _json_ok(job, 202 if job.get("status") == "running" else 200)

    @app.get("/api/sensors/deep-scan")
    def deep_scan_status():
        # Public: the SDR repository is read-only sensor telemetry, the same
        # class of data the anonymous dashboard already shows.
        with state.lock:
            job = dict(state.deep_scan)
        return _json_ok(job, 202 if job.get("status") == "running" else 200)

    # Warm the cache before the first phone opens the page, then keep a recent
    # history even when no browser is connected. request_telemetry() remains
    # non-blocking and coalesces overlapping refreshes, so this loop cannot
    # create parallel Redfish walks against the resource-constrained iDRAC8.
    initial_config, _revision = state.connection_snapshot()
    if not app.config["TESTING"] and initial_config.configured:
        sample_interval = min(
            300.0,
            max(10.0, float(app.config["TELEMETRY_SAMPLE_INTERVAL"])),
        )

        def sample_telemetry_forever() -> None:
            while True:
                try:
                    backend.request_telemetry()
                except Exception:
                    # Detailed, password-redacted failures are already kept in
                    # RuntimeState by the refresh worker for authenticated users.
                    pass
                time.sleep(sample_interval)

        sampler = threading.Thread(
            target=sample_telemetry_forever,
            name="idrac-telemetry-sampler",
            daemon=True,
        )
        app.extensions["telemetry_sampler"] = sampler
        sampler.start()

    return app


app = create_app()
