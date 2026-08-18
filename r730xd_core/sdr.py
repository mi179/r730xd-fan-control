"""Parsing and classification of `ipmitool sdr elist all` output.

Both lines used to carry their own copy of these rules. They happened to agree
on classification - the desktop's extra `"temperature"` test was dead code,
already covered by `"temp"` - but every string constant had drifted (`"RAW"` vs
`"raw"`, `"UNPARSED RECORD"` vs `""`). Agreement by coincidence is not a
guarantee; one definition is.

Category values are lower case because they travel in the Web JSON API and
`app.js` reads them. That is a published contract, so it wins over the
desktop's internal-only upper case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORY_TEMPERATURE = "temperature"
CATEGORY_FAN = "fan"
CATEGORY_POWER = "power"
CATEGORY_VOLTAGE = "voltage"
CATEGORY_CURRENT = "current"
CATEGORY_SYSTEM = "system"

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

# Statuses the BMC uses for "nothing to see here". Anything else on a parsed
# record is treated as an alert, so an unrecognised state is loud, not silent.
_CALM_STATUSES = frozenset({"", "ok", "ns", "na", "disabled"})


def sensor_category(name: str, reading: str) -> str:
    searchable = f"{name} {reading}".casefold()
    if "degrees c" in searchable or "temp" in searchable:
        return CATEGORY_TEMPERATURE
    if "rpm" in searchable or "fan" in searchable:
        return CATEGORY_FAN
    if "watt" in searchable or "power" in searchable:
        return CATEGORY_POWER
    if "volt" in searchable:
        return CATEGORY_VOLTAGE
    if "amp" in searchable or "current" in searchable:
        return CATEGORY_CURRENT
    return CATEGORY_SYSTEM


def extract_reading_number(reading: str) -> float | None:
    match = _NUMBER.search(reading)
    return round(float(match.group(0)), 2) if match else None


@dataclass(frozen=True, slots=True)
class SdrRecord:
    name: str
    sensor_id: str
    status: str
    entity: str
    reading: str
    raw: str
    parsed: bool = True

    @property
    def category(self) -> str:
        return sensor_category(self.name, self.reading)

    @property
    def is_alert(self) -> bool:
        return self.parsed and self.status.strip().casefold() not in _CALM_STATUSES

    @property
    def value(self) -> float | None:
        return extract_reading_number(self.reading)


def parse_sdr_records(output: str) -> list[SdrRecord]:
    """Keep every non-empty line, including ones that do not fit the layout.

    Dropping unrecognised rows would quietly shrink a "full scan"; marking them
    `parsed=False` keeps them visible without pretending they were understood.
    """
    records: list[SdrRecord] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) >= 5:
            records.append(
                SdrRecord(
                    name=fields[0] or "Unnamed sensor",
                    sensor_id=fields[1],
                    status=fields[2],
                    entity=fields[3],
                    reading=" | ".join(fields[4:]),
                    raw=line,
                )
            )
        else:
            records.append(
                SdrRecord(
                    name=line,
                    sensor_id="",
                    status="raw",
                    entity="",
                    reading="",
                    raw=line,
                    parsed=False,
                )
            )
    return records
