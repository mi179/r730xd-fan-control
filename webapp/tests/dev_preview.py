"""Run the whole Web console locally against a fake iDRAC.

No hardware is contacted. Every ipmitool invocation and Redfish GET is answered
from synthetic data inside this process, and any fan-control `raw` command is
recorded and swallowed rather than executed -- so this script cannot change a
real server's thermal state even if pointed at a live network.

Unlike the `live_*.py` scripts (which talk to the deployed instance), this one
needs nothing but the repo: it is how you iterate on templates/static without a
server, an iDRAC, or a container.

By default the fake reproduces two real iDRAC8 firmware 2.70 quirks that the UI
has to cope with, because a preview that only shows the happy path is useless
for checking the degraded states:

* `PowerMetrics` comes back all zeros next to a live wattage (E-032), so the
  power page must fall back to statistics over stored samples (D-023).
* `sdr elist all` dies with SIGSEGV partway through, before it reaches the
  power / voltage / current records (E-031), so the full scan must render a
  labelled partial result (D-022).

Usage::

    .venv-win/Scripts/python.exe webapp/tests/dev_preview.py [--port 8099] [--alerts]

Then open http://127.0.0.1:8099 and unlock the control deck with root /
devpassword. `--alerts` makes one sensor report Warning so the alert banner and
the warning row styling can be checked.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

# Set before importing app: create_app() reads configuration at import/creation
# time. These values are throwaway and only ever reach the fake backends below.
os.environ.setdefault("FLASK_SECRET_KEY", "dev-only-secret-" + "0" * 48)
os.environ.setdefault("AUTH_MODE", "idrac")
os.environ.setdefault("REQUIRE_ORIGIN", "false")
os.environ.setdefault("IDRAC_HOST", "192.0.2.10")
os.environ.setdefault("IDRAC_USER", "root")
os.environ.setdefault("IDRAC_PASSWORD", "devpassword")
os.environ.setdefault("TELEMETRY_CACHE_TTL", "2")
os.environ.setdefault("TELEMETRY_SAMPLE_INTERVAL", "10")
os.environ.setdefault("HISTORY_MAX_SAMPLES", "240")
# Outside the repo, so a preview run never leaves an untracked database behind.
os.environ.setdefault(
    "TELEMETRY_DB_PATH", str(Path(tempfile.gettempdir()) / "r730xd-preview-telemetry.db")
)
os.environ.setdefault("TELEMETRY_FLUSH_INTERVAL", "2")
os.environ.setdefault("TELEMETRY_FLUSH_THRESHOLD", "2")
# MAC discovery would try to read the host ARP table; the fake host has none.
os.environ.pop("IDRAC_MAC", None)

from app import CommandResult, create_app  # noqa: E402

START = time.monotonic()
WARN_SENSOR = "Exhaust Temp"

TEMP_SENSORS = [
    ("Inlet Temp", 4.0, 22.0, 0.0),
    ("Exhaust Temp", 6.0, 38.0, 0.8),
    ("Temp CPU1", 9.0, 54.0, 1.6),
    ("Temp CPU2", 9.0, 51.0, 2.4),
]
FAN_SENSORS = [f"Fan{index} RPM" for index in range(1, 7)]


def _wave(period: float, amplitude: float, offset: float, phase: float = 0.0) -> float:
    """A slow sine so the dashboard shows movement without random jitter."""

    elapsed = time.monotonic() - START
    return offset + amplitude * math.sin(2 * math.pi * (elapsed / period) + phase)


def _temperatures() -> list[tuple[str, float]]:
    return [
        (name, round(_wave(90 + index * 13, amplitude, base, phase), 1))
        for index, (name, amplitude, base, phase) in enumerate(TEMP_SENSORS)
    ]


def _fans() -> list[tuple[str, int]]:
    return [
        (name, int(_wave(120 + index * 9, 260, 3960 + index * 40, index * 0.7)))
        for index, name in enumerate(FAN_SENSORS)
    ]


def _watts() -> float:
    return round(_wave(75, 34, 168) + random.uniform(-3, 3), 1)


def _full_sdr() -> str:
    """A believable R730xd SDR repository, including the awkward records."""

    lines = [
        f"{name} | {index:02X}h | ok | 7.1 | {value} degrees C"
        for index, (name, value) in enumerate(_temperatures(), start=1)
    ]
    lines += [
        f"{name} | {0x30 + index:02X}h | ok | 7.1 | {value} RPM"
        for index, (name, value) in enumerate(_fans())
    ]
    watts = _watts()
    lines += [
        f"Pwr Consumption | 77h | ok | 7.1 | {watts} Watts",
        "PS1 Status | 70h | ok | 10.1 | Presence detected",
        "PS2 Status | 71h | cr | 10.2 | Presence detected, Failure detected",
        "PS Redundancy | 77h | nc | 21.1 | Redundancy Lost",
        "Voltage 1 PS1 | 6Ah | ok | 10.1 | 236 Volts",
        "Voltage 2 PS2 | 6Bh | ok | 10.2 | 234 Volts",
        "VCORE PG | 08h | ok | 3.1 | State Deasserted",
        "3.3V PG | 12h | ok | 7.1 | State Deasserted",
        "Current 1 | 6Dh | ok | 10.1 | 0.60 Amps",
        "Intrusion | 73h | ok | 7.1 | ",
        "Drive 0 | A0h | ok | 26.1 | Drive Present",
        "Cable SAS A0 | C0h | ok | 26.1 | Connected",
        "Riser Config | B0h | ok | 7.1 | Connected",
        "Fan Redundancy | 78h | ok | 7.1 | Fully Redundant",
        "CPU Usage | 90h | ok | 7.1 | 34 unspecified",
        "MEM Usage | 92h | ok | 7.1 | 41 unspecified",
        "OS Watchdog | 71h | ok | 7.1 | ",
        # The parser must survive a line that carries no pipe separators.
        "Unknown blob line without pipes",
    ]
    lines += [
        f"DIMM {slot}{index} | {0xD0 + index:02X}h | ok | 32.1 | "
        f"{round(30 + (index * 1.7), 1)} degrees C"
        for slot in "ABCDEFGH"
        for index in (1, 2, 3)
    ]
    return "\n".join(lines)


class FakeIpmi:
    """Answers every ipmitool invocation locally; writes are recorded, not sent."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, ...]] = []

    def run(self, config, arguments, timeout=None):  # noqa: ARG002
        args = tuple(arguments)
        if args and args[0] == "raw":
            # The one branch that matters for safety: fan-control commands are
            # captured here and never reach a network.
            self.writes.append(args)
            print(f"[fake-ipmi] swallowed write: {' '.join(args)}")
            return CommandResult(0, "", "", 0.02)
        if args == ("mc", "info"):
            return CommandResult(
                0,
                "Device ID                 : 32\n"
                "Firmware Revision         : 2.70\n"
                "Manufacturer Name         : DELL Inc.\n"
                "Product Name              : Fake iDRAC8 (preview)\n",
                "",
                0.05,
            )
        if args == ("sdr", "type", "Temperature"):
            body = "\n".join(
                f"{name} | {index:02X}h | ok | 7.1 | {value} degrees C"
                for index, (name, value) in enumerate(_temperatures(), start=1)
            )
            return CommandResult(0, body, "", 0.05)
        if args == ("sdr", "type", "Fan"):
            body = "\n".join(
                f"{name} | {0x30 + index:02X}h | ok | 7.1 | {value} RPM"
                for index, (name, value) in enumerate(_fans())
            )
            return CommandResult(0, body, "", 0.05)
        if args == ("dcmi", "power", "reading"):
            watts = _watts()
            return CommandResult(
                0,
                f"Instantaneous power reading:   {watts} Watts\n"
                f"Minimum power over sample duration:  {watts - 40:.1f} Watts\n"
                f"Maximum power over sample duration:  {watts + 55:.1f} Watts\n"
                f"Average power reading over sample period: {watts - 6:.1f} Watts\n",
                "",
                0.05,
            )
        if args == ("sdr", "elist", "all"):
            # Reproduce E-031: ipmitool prints most records, then dies before
            # reaching the power / voltage / current entries.
            survived = [
                line
                for line in _full_sdr().splitlines()
                if not any(unit in line for unit in ("Watts", "Volts", "Amps"))
            ]
            return CommandResult(139, "\n".join(survived), "Segmentation fault", 4.2)
        return CommandResult(0, "", "", 0.05)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def build_redfish(with_alerts: bool):
    def fake_redfish(url: str, **_kwargs) -> FakeResponse:
        if url.endswith("/redfish/v1/Chassis"):
            return FakeResponse(
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"}]}
            )
        if url.endswith("/Thermal"):
            return FakeResponse(
                {
                    "Temperatures": [
                        {
                            "Name": name,
                            "ReadingCelsius": value,
                            "Status": {
                                "Health": (
                                    "Warning"
                                    if with_alerts and name == WARN_SENSOR
                                    else "OK"
                                ),
                                "State": "Enabled",
                            },
                            "UpperThresholdNonCritical": 47 if "Inlet" in name else 80,
                            "UpperThresholdCritical": 52 if "Inlet" in name else 90,
                        }
                        for name, value in _temperatures()
                    ],
                    "Fans": [
                        {
                            "Name": name,
                            "Reading": value,
                            "ReadingUnits": "RPM",
                            "Status": {"Health": "OK", "State": "Enabled"},
                        }
                        for name, value in _fans()
                    ],
                }
            )
        if url.endswith("/Power"):
            return FakeResponse(
                {
                    "PowerControl": [
                        {
                            "PowerConsumedWatts": _watts(),
                            "PowerCapacityWatts": 896,
                            "PowerAllocatedWatts": 896,
                            # E-032: live wattage next to zeroed metrics.
                            "PowerMetrics": {
                                "AverageConsumedWatts": 0,
                                "MinConsumedWatts": 0,
                                "MaxConsumedWatts": 0,
                            },
                        }
                    ]
                }
            )
        if "/Managers" in url:
            return FakeResponse({"Members": []})
        return FakeResponse({})

    return fake_redfish


def seed_history(app, minutes: int = 25) -> None:
    """Backfill the deque so the trend chart has something to draw immediately."""

    state = app.extensions["r730xd_backend"].state
    now = datetime.now(UTC)
    with state.lock:
        for step in range(minutes * 4, 0, -1):
            phase = step / 9.0
            state.history.append(
                {
                    "timestamp": (now - timedelta(seconds=step * 15))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "max_temp_c": round(54 + 5 * math.sin(phase), 1),
                    "avg_fan_rpm": round(4000 + 300 * math.sin(phase * 0.7), 0),
                    "power_watts": round(168 + 30 * math.sin(phase * 1.3), 1),
                    "source": "redfish",
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument(
        "--alerts",
        action="store_true",
        help=f"make {WARN_SENSOR} report Warning, to exercise the alert banner",
    )
    options = parser.parse_args()

    app = create_app(
        ipmi_runner=FakeIpmi(), redfish_get=build_redfish(options.alerts)
    )
    # Templates are the main thing being iterated on here.
    app.jinja_env.auto_reload = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    seed_history(app)

    print(f"fake-iDRAC preview on http://127.0.0.1:{options.port}")
    print("unlock the control deck with root / devpassword")
    print("no hardware is contacted; fan-control commands are swallowed")
    threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=options.port, debug=False, threaded=True
        ),
        daemon=False,
    ).start()


if __name__ == "__main__":
    main()
