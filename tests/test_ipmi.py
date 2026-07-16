from __future__ import annotations

import unittest
from pathlib import Path

from r730xd_fan.config import IpmiSettings
from r730xd_fan.ipmi import (
    auto_mode_request,
    build_command,
    manual_mode_request,
    parse_sensor_output,
    sensor_snapshot_request,
    speed_request,
)


class IpmiCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = IpmiSettings(
            host="192.168.5.151",
            username="root",
            password="top-secret",
            executable=Path("ipmitool.exe"),
        )

    def test_password_is_not_in_process_arguments(self) -> None:
        command = build_command(self.settings, speed_request(15))
        self.assertIn("-E", command)
        self.assertNotIn("-P", command)
        self.assertNotIn("top-secret", command)

    def test_speed_percentage_is_encoded_as_hex(self) -> None:
        self.assertEqual(speed_request(10).arguments[-1], "0x0a")
        self.assertEqual(speed_request(15).arguments[-1], "0x0f")
        self.assertEqual(speed_request(20).arguments[-1], "0x14")
        self.assertEqual(speed_request(100).arguments[-1], "0x64")

    def test_speed_range_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            speed_request(4)
        with self.assertRaises(ValueError):
            speed_request(101)

    def test_mode_commands_match_dell_raw_protocol(self) -> None:
        self.assertEqual(manual_mode_request().arguments, ("raw", "0x30", "0x30", "0x01", "0x00"))
        self.assertEqual(auto_mode_request().arguments, ("raw", "0x30", "0x30", "0x01", "0x01"))

    def test_sensor_snapshot_reads_all_sdr_records(self) -> None:
        request = sensor_snapshot_request()
        self.assertEqual(request.arguments, ("sdr", "elist", "all"))
        self.assertEqual(request.timeout_seconds, 60)
        command = build_command(self.settings, request)
        self.assertNotIn("top-secret", command)
        self.assertIn("-E", command)

    def test_sensor_parser_preserves_known_and_unknown_rows(self) -> None:
        output = """\
Inlet Temp       | 01h | ok | 7.1 | 24 degrees C
Fan1 RPM         | 30h | ok | 7.1 | 4080 RPM
PS1 Status       | 70h | cr | 10.1 | Presence detected
Dell vendor record without separators
"""
        readings = parse_sensor_output(output)

        self.assertEqual(len(readings), 4)
        self.assertEqual(readings[0].category, "TEMPERATURE")
        self.assertEqual(readings[1].category, "FAN")
        self.assertTrue(readings[2].is_alert)
        self.assertFalse(readings[3].parsed)
        self.assertEqual(readings[3].raw, "Dell vendor record without separators")


if __name__ == "__main__":
    unittest.main()
