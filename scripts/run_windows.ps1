$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $Python = "py.exe"
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    $Python = "python.exe"
} else {
    throw "未找到 Windows Python。请先运行 scripts\setup_windows.ps1"
}
Push-Location $ProjectRoot
try {
    & $Python "main.py"
} finally {
    Pop-Location
}
