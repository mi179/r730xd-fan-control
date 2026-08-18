from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from r730xd_fan.config import IpmiSettings
from r730xd_fan.ipmi import (
    auto_mode_request,
    build_command,
    connection_test_request,
    execute,
    manual_mode_request,
    parse_sensor_output,
    redact_and_limit,
    sensor_snapshot_request,
    speed_request,
    summarize_key_readings,
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
        # Lower case because the value travels in the Web JSON API (D-027).
        self.assertEqual(readings[0].category, "temperature")
        self.assertEqual(readings[1].category, "fan")
        self.assertTrue(readings[2].is_alert)
        self.assertFalse(readings[3].parsed)
        self.assertEqual(readings[3].raw, "Dell vendor record without separators")


class KeyReadingTests(unittest.TestCase):
    SNAPSHOT = "\n".join(
        (
            "Inlet Temp | 04h | ok | 7.1 | 23 degrees C",
            "Exhaust Temp | 01h | ok | 7.1 | 35 degrees C",
            "Temp | 0Eh | ok | 3.1 | 48 degrees C",
            "Temp | 0Fh | ns | 3.2 | Disabled",
            "Pwr Consumption | 77h | ok | 7.1 | 133 Watts",
        )
    )

    def test_slots_are_matched_by_sensor_name(self) -> None:
        cards = summarize_key_readings(parse_sensor_output(self.SNAPSHOT))
        self.assertEqual(
            [(card.label, card.value, card.unit) for card in cards],
            [
                ("进风温度", "23", "°C"),
                ("排风温度", "35", "°C"),
                ("CPU 温度", "48", "°C"),
                ("实时功耗", "133", "W"),
            ],
        )
        self.assertTrue(all(card.status == "ok" for card in cards))

    def test_absent_sensor_reports_unknown_rather_than_a_wrong_number(self) -> None:
        cards = summarize_key_readings(
            parse_sensor_output("Fan1A RPM | 30h | ok | 7.1 | 5880 RPM")
        )
        self.assertTrue(all(card.value == "--" for card in cards))
        self.assertTrue(all(card.status == "unknown" for card in cards))

    def test_bmc_flagged_sensor_is_marked_alert(self) -> None:
        cards = summarize_key_readings(
            parse_sensor_output("Inlet Temp | 04h | cr | 7.1 | 61 degrees C")
        )
        self.assertEqual(cards[0].value, "61")
        self.assertEqual(cards[0].status, "alert")

    def test_a_temperature_is_never_reused_across_two_slots(self) -> None:
        """Only an inlet sensor exists; CPU must stay blank, not echo the inlet."""
        cards = summarize_key_readings(
            parse_sensor_output("Inlet Temp | 04h | ok | 7.1 | 23 degrees C")
        )
        self.assertEqual(cards[0].value, "23")
        self.assertEqual(cards[1].value, "--")
        self.assertEqual(cards[2].value, "--")


class RedactionTests(unittest.TestCase):
    """The password must not survive into anything a human can read.

    Mirrors _redact_and_limit in webapp/app.py, applied at the same place:
    where subprocess output enters the program.
    """

    SECRET = "hunter2-idrac"

    def test_execute_redacts_command_output(self) -> None:
        settings = IpmiSettings(
            host="198.51.100.5",
            username="root",
            password=self.SECRET,
            executable=Path("ipmitool.exe"),
        )
        completed = subprocess.CompletedProcess(
            args=["ipmitool"],
            returncode=0,
            stdout=f"opened session for root/{self.SECRET}\n",
            stderr=f"retrying with {self.SECRET}\n",
        )
        with patch("r730xd_fan.ipmi.subprocess.run", return_value=completed):
            result = execute(settings, connection_test_request())

        self.assertNotIn(self.SECRET, result.stdout)
        self.assertNotIn(self.SECRET, result.stderr)
        self.assertIn("[REDACTED]", result.stdout)

    def test_output_is_length_capped(self) -> None:
        capped = redact_and_limit("x" * (600 * 1024), "")
        self.assertLessEqual(len(capped), 512 * 1024)

    def test_empty_password_leaves_output_untouched(self) -> None:
        self.assertEqual(redact_and_limit("  plain text  ", ""), "plain text")


if __name__ == "__main__":
    unittest.main()
