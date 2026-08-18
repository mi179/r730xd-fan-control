"""Keep the iDRAC password out of anything a human or a log file can read.

Applied where subprocess output enters the program, so everything downstream -
event log, sensor tables, JSON responses - is already clean. Both product lines
share this because both hand raw ipmitool output to a user interface.
"""

from __future__ import annotations

# A stuck `sdr elist all` can emit far more than a UI should ever try to render.
SAFE_OUTPUT_LIMIT = 512 * 1024


def redact_and_limit(value: str, password: str) -> str:
    if password:
        value = value.replace(password, "[REDACTED]")
    return value[:SAFE_OUTPUT_LIMIT].strip()


def safe_exception(exc: Exception, password: str) -> str:
    return redact_and_limit(str(exc), password)[:300] or "未知错误"
