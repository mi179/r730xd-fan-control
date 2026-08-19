from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Deliberately empty. A hardcoded address goes stale the first time DHCP moves
# the BMC, and then the console looks configured and simply times out - which is
# exactly how this bit its own author. Empty means the window says 需要配置, and
# the connection dialog offers to scan the LAN for it instead (D-028).
DEFAULT_HOST = ""
DEFAULT_USER = "root"


def ipmitool_candidates() -> tuple[Path, ...]:
    """Return platform-aware ipmitool candidates in preference order."""
    candidates: list[Path] = []
    configured = os.getenv("IPMITOOL_PATH", "").strip()
    if configured:
        candidates.append(Path(configured))

    if os.name == "nt":
        program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
        program_files = os.getenv("ProgramFiles", r"C:\Program Files")
        candidates.extend(
            [
                Path(r"D:\Program Files (x86)\Dell\SysMgt\bmc\ipmitool.exe"),
                Path(program_files_x86) / "Dell" / "SysMgt" / "bmc" / "ipmitool.exe",
                Path(program_files) / "Dell" / "SysMgt" / "bmc" / "ipmitool.exe",
                Path(r"C:\OpenManage\bmc\ipmitool.exe"),
                Path(r"C:\OpenManage\ipmitool.exe"),
            ]
        )
    else:
        candidates.extend(
            [
                Path("/mnt/d/Program Files (x86)/Dell/SysMgt/bmc/ipmitool.exe"),
                Path("/usr/bin/ipmitool"),
                Path("/usr/local/bin/ipmitool"),
            ]
        )

    on_path = shutil.which("ipmitool.exe") or shutil.which("ipmitool")
    if on_path:
        candidates.append(Path(on_path))

    # Preserve ordering while removing duplicates.
    return tuple(dict.fromkeys(candidates))


def discover_ipmitool() -> Path:
    for candidate in ipmitool_candidates():
        if candidate.is_file():
            return candidate
    candidates = ipmitool_candidates()
    return candidates[0] if candidates else Path("ipmitool")


@dataclass(frozen=True, slots=True)
class IpmiSettings:
    host: str = DEFAULT_HOST
    username: str = DEFAULT_USER
    password: str = ""
    executable: Path = Path("ipmitool")
    timeout_seconds: int = 15

    @classmethod
    def from_environment(cls) -> IpmiSettings:
        return cls(
            host=os.getenv("IDRAC_HOST", DEFAULT_HOST).strip(),
            username=os.getenv("IDRAC_USER", DEFAULT_USER).strip(),
            password=os.getenv("IPMI_PASSWORD", ""),
            executable=discover_ipmitool(),
        )
