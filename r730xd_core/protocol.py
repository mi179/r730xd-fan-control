"""Dell iDRAC fan-control raw IPMI protocol - one definition, both product lines.

Before this module the desktop console and the Web console each carried their
own copy of these byte sequences, and `webapp/app.py` additionally built the
speed command from an inline literal with no constant behind it. Three copies of
the commands that physically change how a server cools itself.

Nothing here does I/O. Each line wraps these arguments in its own runner,
because those legitimately differ: the Web runner whitelists the child
environment and carries an IPMI port, the desktop one suppresses a console
window on Windows.
"""

from __future__ import annotations

# `ipmitool raw 0x30 0x30 0x01 0x00` - hand fan control to the operator.
MANUAL_MODE_ARGS: tuple[str, ...] = ("raw", "0x30", "0x30", "0x01", "0x00")

# `... 0x01 0x01` - give it back to the BMC. This is the way out, and both
# front ends must keep it reachable at all times.
AUTO_MODE_ARGS: tuple[str, ...] = ("raw", "0x30", "0x30", "0x01", "0x01")

# Any command starting with one of these changes hardware state. The Web line
# asserts on this set to prove no write ever reaches an anonymous route.
WRITE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("raw", "0x30", "0x30", "0x01"),
    ("raw", "0x30", "0x30", "0x02"),
)

MIN_PERCENT = 5
MAX_PERCENT = 100


def speed_args(percent: int) -> tuple[str, ...]:
    """Arguments for a fixed fan duty cycle.

    The floor is not cosmetic: below it the fans cannot keep a loaded R730xd
    within its thermal envelope, so the bound is enforced here rather than in
    each caller's UI.
    """
    if not MIN_PERCENT <= percent <= MAX_PERCENT:
        raise ValueError(f"风扇百分比必须在 {MIN_PERCENT} 到 {MAX_PERCENT} 之间")
    return ("raw", "0x30", "0x30", "0x02", "0xff", f"0x{percent:02x}")
