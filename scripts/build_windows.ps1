$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"
$AppName = "R730xdFanConsole-AllInOne-v0.4.0"
$Output = Join-Path $ProjectRoot "dist\$AppName.exe"
$BmcMsi = "C:\OpenManage\BMC.msi"

if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot "setup_windows.ps1")
}
if (-not (Test-Path -LiteralPath $BmcMsi)) {
    throw "Dell BMC.msi was not found: $BmcMsi"
}
$ExpectedMsiHash = "13F2179F622A0AB536B2FA26772AC2E05B5F95993C15A45DD99429F20EC09E15"
$ActualMsiHash = (Get-FileHash -LiteralPath $BmcMsi -Algorithm SHA256).Hash
if ($ActualMsiHash -ne $ExpectedMsiHash) {
    throw "Dell BMC.msi hash mismatch; build aborted."
}
$MsiSignature = Get-AuthenticodeSignature -LiteralPath $BmcMsi
if ($MsiSignature.Status -ne "Valid") {
    throw "Dell BMC.msi signature is invalid: $($MsiSignature.StatusMessage)"
}

Push-Location $ProjectRoot
try {
    & $Python -m pip install "pyinstaller>=6.14,<7"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller setup failed with exit code $LASTEXITCODE" }
    if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Force }
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name $AppName `
        --collect-all customtkinter `
        --add-data "$BmcMsi;payload" `
        --distpath "dist" `
        --workpath "build\pyinstaller" `
        --specpath "build" `
        "main.py"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }

    if (-not (Test-Path -LiteralPath $Output)) {
        throw "Build finished without the expected output: $Output"
    }

    $ArchiveViewer = Join-Path $ProjectRoot ".venv-win\Scripts\pyi-archive_viewer.exe"
    $ArchiveListing = & $ArchiveViewer -l $Output 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Archive inspection failed with exit code $LASTEXITCODE" }
    # pyi-archive_viewer renders archive names as Python repr strings, escaping the slash.
    if (-not ($ArchiveListing | Select-String -SimpleMatch "payload\\BMC.msi")) {
        throw "Build archive does not contain payload\BMC.msi"
    }

    $File = Get-Item -LiteralPath $Output
    Write-Host "BUILD_OK $($File.FullName) $($File.Length) bytes"
} finally {
    Pop-Location
}
