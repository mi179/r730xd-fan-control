"""Pure derivation: readings and state in, display text and a tone out.

Nothing here imports tkinter or knows a single colour value. Functions return a
*tone* - one of TONES below - and the view maps that to a palette entry. This is
the same split the Web line uses, where ``_public_telemetry_value`` decides what
a number means and ``app.css`` decides what it looks like.

Everything in this module is a free function over plain data, so it is testable
without a display. The desktop GUI tests can only run on Windows; these run
anywhere, including the Linux CI runner.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .ipmi import SensorReading

# The whole colour vocabulary of the product. "alert" and "warn" are the only
# tones allowed to be saturated (D-014): red means the BMC flagged something,
# amber means the operator needs to act. Everything else is monochrome.
TONES = ("neutral", "muted", "ok", "warn", "alert")

CATEGORY_LABELS = {
    "TEMPERATURE": "温度",
    "FAN": "风扇",
    "POWER": "功耗",
    "VOLTAGE": "电压",
    "CURRENT": "电流",
    "SYSTEM": "其他",
}

CATEGORY_ORDER = {
    "TEMPERATURE": 0,
    "FAN": 1,
    "POWER": 2,
    "VOLTAGE": 3,
    "CURRENT": 4,
    "SYSTEM": 5,
}

# The three states the fan mode can be in. "unknown" is not a failure: the tool
# cannot read back from iDRAC whether manual override is active, it can only
# remember what it did this session. Saying so is more honest than defaulting to
# "auto" and being wrong after a reboot.
MODE_UNKNOWN = "unknown"
MODE_MANUAL = "manual"
MODE_AUTO = "auto"


def connection_status(configured: bool) -> tuple[str, str]:
    """Status shown on the primary window. Never includes host, user or secret."""
    return ("就绪", "ok") if configured else ("需要配置", "warn")


def connection_chip(configured: bool, *, online: bool = False) -> tuple[str, str]:
    if online:
        return "●  iDRAC  在线", "ok"
    text, tone = connection_status(configured)
    return f"●  iDRAC  {text}", tone


def mode_badge(mode: str) -> tuple[str, str]:
    if mode == MODE_MANUAL:
        return "手动接管", "alert"
    if mode == MODE_AUTO:
        return "自动温控", "neutral"
    return "状态未知", "neutral"


def output_status(mode: str, percent: int) -> tuple[str, str]:
    """Right-hand status of the fan output card."""
    if mode == MODE_MANUAL:
        return f"已接管 · {percent}%", "neutral"
    return "未接管", "warn"


def gauge_tone(percent: int) -> str:
    """Colour of the gauge arc. Thresholds are the pre-refactor ones exactly:
    <= 30 plain, 31-59 amber, >= 60 red."""
    if percent >= 60:
        return "alert"
    if percent > 30:
        return "warn"
    return "neutral"


def custom_speed_label(percent: int) -> str:
    return f"自定义 {percent}%"


def card_health(status: str) -> tuple[str, str]:
    """Badge on a reading card. Only a BMC-flagged reading goes red."""
    if status == "alert":
        return "异常", "alert"
    if status == "ok":
        return "正常", "muted"
    return "未知", "muted"


def readings_meta(count: int, updated_at: str, poll_seconds: int) -> str:
    return f"更新于 {updated_at} · 共 {count} 条记录 · 每 {poll_seconds} 秒自动刷新"


def reading_tone(reading: SensorReading) -> str:
    """Tone for one SDR row's status chip."""
    if not reading.parsed:
        return "muted"
    if reading.is_alert:
        return "alert"
    if reading.status.strip().casefold() == "ok":
        return "ok"
    return "muted"


def filter_readings(
    readings: Iterable[SensorReading],
    query: str = "",
    *,
    alerts_only: bool = False,
) -> list[SensorReading]:
    """Search across name, category, reading and status; optionally alerts only."""
    needle = query.strip().casefold()
    matches: list[SensorReading] = []
    for reading in readings:
        if alerts_only and not reading.is_alert:
            continue
        if needle:
            haystack = " ".join(
                (reading.name, reading.category, reading.reading, reading.status)
            ).casefold()
            if needle not in haystack:
                continue
        matches.append(reading)
    return matches


def group_by_category(
    readings: Sequence[SensorReading],
) -> list[tuple[str, list[SensorReading]]]:
    """Order by category, keeping the original order inside each group.

    Returns localised group headings paired with their rows, so the view only
    has to lay them out.
    """
    ordered = sorted(
        enumerate(readings),
        key=lambda item: (CATEGORY_ORDER.get(item[1].category, 99), item[0]),
    )
    groups: list[tuple[str, list[SensorReading]]] = []
    for _index, reading in ordered:
        label = CATEGORY_LABELS.get(reading.category, reading.category)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(reading)
        else:
            groups.append((label, [reading]))
    return groups


def sensor_summary(readings: Sequence[SensorReading]) -> tuple[str, str]:
    temperatures = sum(item.category == "TEMPERATURE" for item in readings)
    fans = sum(item.category == "FAN" for item in readings)
    alerts = sum(item.is_alert for item in readings)
    text = (
        f"共 {len(readings)} 条  ·  温度 {temperatures}  ·  "
        f"风扇 {fans}  ·  告警 {alerts}"
    )
    return text, ("alert" if alerts else "ok")


def sensor_log_line(readings: Sequence[SensorReading]) -> str:
    temperatures = sum(item.category == "TEMPERATURE" for item in readings)
    fans = sum(item.category == "FAN" for item in readings)
    alerts = sum(item.is_alert for item in readings)
    return f"已读取 {len(readings)} 条记录；温度 {temperatures}，风扇 {fans}，告警 {alerts}。"


def scan_timing(updated_at: str, elapsed_seconds: float) -> str:
    return f"更新于 {updated_at}  ·  {elapsed_seconds:.2f} s"
