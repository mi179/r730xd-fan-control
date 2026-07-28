from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import IpmiSettings

MANUAL_MODE_RAW = ("0x30", "0x30", "0x01", "0x00")
AUTO_MODE_RAW = ("0x30", "0x30", "0x01", "0x01")


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


@dataclass(frozen=True, slots=True)
class SensorReading:
    name: str
    sensor_id: str
    status: str
    entity: str
    reading: str
    raw: str
    parsed: bool = True

    @property
    def category(self) -> str:
        searchable = f"{self.name} {self.reading}".casefold()
        if "degrees c" in searchable or "temperature" in searchable or "temp" in searchable:
            return "TEMPERATURE"
        if "rpm" in searchable or "fan" in searchable:
            return "FAN"
        if "watt" in searchable or "power" in searchable:
            return "POWER"
        if "volt" in searchable:
            return "VOLTAGE"
        if "amp" in searchable or "current" in searchable:
            return "CURRENT"
        return "SYSTEM"

    @property
    def is_alert(self) -> bool:
        normalized = self.status.strip().casefold()
        return self.parsed and normalized not in {"", "ok", "ns", "na", "disabled"}


def manual_mode_request() -> IpmiRequest:
    return raw_request("关闭自动温控", MANUAL_MODE_RAW)


def auto_mode_request() -> IpmiRequest:
    return raw_request("恢复自动温控", AUTO_MODE_RAW)


def speed_request(percent: int) -> IpmiRequest:
    if not 5 <= percent <= 100:
        raise ValueError("风扇百分比必须在 5 到 100 之间")
    return raw_request(
        f"设置风扇为 {percent}%",
        ("0x30", "0x30", "0x02", "0xff", f"0x{percent:02x}"),
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


def raw_request(label: str, values: Sequence[str]) -> IpmiRequest:
    arguments = ("raw", *tuple(values))
    return IpmiRequest(label, arguments, " ".join(arguments))


def parse_sensor_output(output: str) -> list[SensorReading]:
    """Parse ``sdr elist all`` output while preserving every non-empty row."""
    readings: list[SensorReading] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) >= 5:
            readings.append(
                SensorReading(
                    name=fields[0] or "UNNAMED SENSOR",
                    sensor_id=fields[1],
                    status=fields[2],
                    entity=fields[3],
                    reading=" | ".join(fields[4:]),
                    raw=line,
                )
            )
        else:
            readings.append(
                SensorReading(
                    name=line,
                    sensor_id="",
                    status="RAW",
                    entity="",
                    reading="UNPARSED RECORD",
                    raw=line,
                    parsed=False,
                )
            )
    return readings


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
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
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
