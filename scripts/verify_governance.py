#!/usr/bin/env python3
"""Governance self-check for the r730xd_fan repository.

Run before committing:

    python3 scripts/verify_governance.py

Checks (failures exit non-zero):
1. All governance files exist.
2. EVIDENCE.md has at least one E- row.
3. No credential or artifact files are tracked by git.
4. Git history exists.
5. Any .ps1 containing non-ASCII characters is saved as UTF-8 with BOM.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GOVERNANCE_FILES = [
    "AGENTS.md",
    "PROJECT.md",
    "SPEC.md",
    "DECISIONS.md",
    "STATUS.md",
    "PLAN.md",
    "TASKS.md",
    "EVIDENCE.md",
    "HANDOFF.md",
]

FORBIDDEN_TRACKED_SUFFIXES = (
    "/.env",
    ".tar.gz",
    ".exe",
    "BMC.msi",
)

FORBIDDEN_TRACKED_PREFIXES = (
    "webapp/secrets/",
    "dist/",
    "build/",
)


def is_forbidden(tracked_path: str) -> bool:
    if tracked_path.startswith(FORBIDDEN_TRACKED_PREFIXES):
        return True
    if tracked_path.endswith(FORBIDDEN_TRACKED_SUFFIXES):
        return True
    return tracked_path == ".env"


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> int:
    failures: list[str] = []

    for name in GOVERNANCE_FILES:
        if not (REPO_ROOT / name).is_file():
            failures.append(f"missing governance file: {name}")

    evidence = REPO_ROOT / "EVIDENCE.md"
    if evidence.is_file() and "| E-" not in evidence.read_text(encoding="utf-8"):
        failures.append("EVIDENCE.md has no E- rows")

    tracked = git("ls-files")
    if tracked.returncode != 0:
        failures.append(f"git ls-files failed: {tracked.stderr.strip()}")
    else:
        for line in tracked.stdout.splitlines():
            if is_forbidden(line):
                failures.append(f"forbidden file tracked in git: {line}")

    history = git("log", "--oneline", "-1")
    if history.returncode != 0 or not history.stdout.strip():
        failures.append("no git history")

    # A built EXE that lags its source is the failure this project has already
    # had twice: once found a month late (E-036), once again the next day. The
    # version number does not change, so nothing about the file says it is old.
    desktop_sources = [
        path
        for directory in ("r730xd_fan", "r730xd_core")
        for path in (REPO_ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    main_py = REPO_ROOT / "main.py"
    if main_py.is_file():
        desktop_sources.append(main_py)
    if desktop_sources:
        newest = max(path.stat().st_mtime for path in desktop_sources)
        for built in sorted((REPO_ROOT / "dist").glob("*.exe")):
            if built.stat().st_mtime < newest:
                failures.append(
                    f"stale build artefact: {built.name} predates the desktop "
                    f"sources; rebuild with scripts/build_windows.ps1 or delete it"
                )

    for script in sorted(REPO_ROOT.rglob("*.ps1")):
        if any(part in {".venv-win", ".venv-wsl", "node_modules"} for part in script.parts):
            continue
        data = script.read_bytes()
        try:
            data.decode("ascii")
            continue
        except UnicodeDecodeError:
            pass
        if not data.startswith(b"\xef\xbb\xbf"):
            failures.append(f"non-ASCII .ps1 without UTF-8 BOM: {script.relative_to(REPO_ROOT)}")

    if failures:
        print("governance check FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("governance check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
