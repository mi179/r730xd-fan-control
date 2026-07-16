from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r730xd_fan.dependency import (
    DependencyInstallError,
    ensure_ipmitool_available,
    program_data_directory,
    system_executable,
    verify_bmc_payload,
)


class DependencyTests(unittest.TestCase):
    def test_existing_ipmitool_skips_installer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ipmitool.exe"
            executable.write_bytes(b"test")
            with (
                patch("r730xd_fan.dependency.discover_ipmitool", return_value=executable),
                patch(
                    "r730xd_fan.dependency.verify_bmc_payload",
                    side_effect=AssertionError("existing-tool path must not inspect the MSI"),
                ),
                patch(
                    "r730xd_fan.dependency._install_with_status",
                    side_effect=AssertionError("existing-tool path must not request elevation"),
                ),
            ):
                result = ensure_ipmitool_available()
        self.assertEqual(result.executable, executable)
        self.assertFalse(result.installed_now)

    def test_tampered_msi_is_rejected_before_elevation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "BMC.msi"
            payload.write_bytes(b"not-a-dell-msi")
            missing_tool = Path(directory) / "missing" / "ipmitool.exe"
            with (
                patch("r730xd_fan.dependency.discover_ipmitool", return_value=missing_tool),
                patch("r730xd_fan.dependency.bundled_bmc_msi", return_value=payload),
                patch("r730xd_fan.dependency._install_with_status") as installer,
                patch("r730xd_fan.dependency._run_elevated_self") as elevation,
            ):
                with self.assertRaises(DependencyInstallError):
                    if os.name == "nt":
                        ensure_ipmitool_available()
                    else:
                        verify_bmc_payload(payload)
            installer.assert_not_called()
            elevation.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows API path resolution")
    def test_privileged_helpers_use_absolute_windows_directories(self) -> None:
        msiexec = system_executable("msiexec.exe")
        program_data = program_data_directory()
        self.assertTrue(msiexec.is_absolute())
        self.assertEqual(msiexec.name.casefold(), "msiexec.exe")
        self.assertTrue(program_data.is_absolute())
        self.assertTrue(program_data.is_dir())


if __name__ == "__main__":
    unittest.main()
