from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from r730xd_core import protocol, sdr
from r730xd_core.redaction import redact_and_limit, safe_exception
from r730xd_core.sdr import SdrRecord, parse_sdr_records

from .config import IpmiSettings

__all__ = [
    "CommandResult",
    "IpmiRequest",
    "KeyReading",
    "SensorReading",
    "auto_mode_request",
    "build_command",
    "connection_test_request",
    "execute",
    "manual_mode_request",
    "parse_sensor_output",
    "redact_and_limit",
    "safe_exception",
    "sensor_snapshot_request",
    "speed_request",
    "summarize_key_readings",
]


@dataclass(frozen=True, slots=True)
class IpmiRequest:
    label: str
    arguments: tuple[str, ...]
    safe_to_log: str
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    request: IpmiRequest
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# The shared record already carries name/status/category/is_alert; the desktop
# keeps the old name so call sites read the same.
SensorReading = SdrRecord


@dataclass(frozen=True, slots=True)
class KeyReading:
    """One card in the readings row: three temperatures plus live power.

    Mirrors the reading cards in webapp/templates/index.html so both front ends
    show the same four numbers, minus the web-only trend chart.
    """

    label: str
    value: str
    unit: str
    detail: str
    status: str  # "ok" | "alert" | "unknown"


def _numeric(reading: str) -> str | None:
    """Card text keeps the number as written, so 23 does not become 23.0."""
    from r730xd_core.sdr import _NUMBER

    match = _NUMBER.search(reading)
    return match.group(0) if match else None


def _card(label: str, unit: str, reading: SensorReading | None) -> KeyReading:
    if reading is None:
        return KeyReading(label, "--", unit, "未找到该传感器", "unknown")
    value = _numeric(reading.reading)
    if value is None:
        # The sensor exists but iDRAC gave no number (disabled slot, absent CPU).
        return KeyReading(label, "--", unit, reading.name, "unknown")
    return KeyReading(
        label,
        value,
        unit,
        reading.name,
        "alert" if reading.is_alert else "ok",
    )


def _first_named(candidates: list[SensorReading], *needles: str) -> SensorReading | None:
    for needle in needles:
        for reading in candidates:
            if needle in reading.name.casefold():
                return reading
    return None


def summarize_key_readings(readings: list[SensorReading]) -> tuple[KeyReading, ...]:
    """Pick the four headline values out of a full ``sdr elist all`` snapshot.

    Slot assignment is by sensor name, not position: an R730xd reports
    ``Inlet Temp``, ``Exhaust Temp`` and one ``Temp`` per CPU, but a different
    chassis or a pulled CPU changes what is present. Unmatched slots stay
    ``--`` rather than borrowing an unrelated sensor.
    """
    temperatures = [
        item
        for item in readings
        if item.parsed and item.category == sdr.CATEGORY_TEMPERATURE
    ]
    inlet = _first_named(temperatures, "inlet")
    exhaust = _first_named(temperatures, "exhaust")
    taken = {id(item) for item in (inlet, exhaust) if item is not None}
    remaining = [item for item in temperatures if id(item) not in taken]
    cpu = _first_named(remaining, "cpu") or (remaining[0] if remaining else None)

    power = next(
        (
            item
            for item in readings
            if item.parsed
            and item.category == sdr.CATEGORY_POWER
            and "watt" in item.reading.casefold()
        ),
        None,
    )

    return (
        _card("进风温度", "°C", inlet),
        _card("排风温度", "°C", exhaust),
        _card("CPU 温度", "°C", cpu),
        _card("实时功耗", "W", power),
    )


def manual_mode_request() -> IpmiRequest:
    return _protocol_request("关闭自动温控", protocol.MANUAL_MODE_ARGS)


def auto_mode_request() -> IpmiRequest:
    return _protocol_request("恢复自动温控", protocol.AUTO_MODE_ARGS)


def speed_request(percent: int) -> IpmiRequest:
    return _protocol_request(
        f"设置风扇为 {percent}%", protocol.speed_args(percent)
    )


def connection_test_request() -> IpmiRequest:
    return IpmiRequest("测试 iDRAC 连接", ("mc", "info"), "mc info")


def sensor_snapshot_request() -> IpmiRequest:
    return IpmiRequest(
        "读取全部传感器",
        ("sdr", "elist", "all"),
        "sdr elist all",
        timeout_seconds=60,
    )


def _protocol_request(label: str, arguments: Sequence[str]) -> IpmiRequest:
    arguments = tuple(arguments)
    return IpmiRequest(label, arguments, " ".join(arguments))


def raw_request(label: str, values: Sequence[str]) -> IpmiRequest:
    return _protocol_request(label, ("raw", *tuple(values)))


def parse_sensor_output(output: str) -> list[SensorReading]:
    """Parse ``sdr elist all`` output while preserving every non-empty row."""
    return parse_sdr_records(output)


def build_command(settings: IpmiSettings, request: IpmiRequest) -> list[str]:
    """Build a password-safe command line.

    Dell ipmitool's ``-E`` switch reads IPMI_PASSWORD from the child environment,
    so the secret never appears in process arguments or the UI log.
    """
    return [
        str(settings.executable),
        "-I",
        "lanplus",
        "-H",
        settings.host,
        "-U",
        settings.username,
        "-E",
        *request.arguments,
    ]


def execute(settings: IpmiSettings, request: IpmiRequest) -> CommandResult:
    _validate_settings(settings)
    command = build_command(settings, request)
    environment = os.environ.copy()
    environment["IPMI_PASSWORD"] = settings.password

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=request.timeout_seconds or settings.timeout_seconds,
        env=environment,
        creationflags=creationflags,
        check=False,
    )
    return CommandResult(
        request=request,
        returncode=completed.returncode,
        stdout=redact_and_limit(completed.stdout or "", settings.password),
        stderr=redact_and_limit(completed.stderr or "", settings.password),
        elapsed_seconds=time.perf_counter() - started,
    )


def _validate_settings(settings: IpmiSettings) -> None:
    if not settings.host:
        raise ValueError("请填写 iDRAC 地址")
    if not settings.username:
        raise ValueError("请填写 iDRAC 用户名")
    if not settings.password:
        raise ValueError("请输入 iDRAC 密码；密码只保存在当前进程内存中")

    executable = Path(settings.executable)
    if executable.parent != Path(".") and not executable.is_file():
        raise FileNotFoundError(f"找不到 ipmitool：{executable}")
