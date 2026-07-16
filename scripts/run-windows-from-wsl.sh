#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_win="$(wslpath -w "$project_dir/scripts/run_windows.ps1")"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$script_win"
