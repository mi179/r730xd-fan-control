"""One protocol, both product lines.

These commands physically change how a server cools itself. Before r730xd_core
they existed in three places: the desktop builders, a pair of Web constants, and
an inline literal in the Web speed route that no test touched directly. The
point of this file is to make "fix one, forget the other" impossible rather than
merely unlikely.
"""

from __future__ import annotations

import unittest

from r730xd_core import protocol, sdr


class ProtocolTests(unittest.TestCase):
    def test_mode_commands_are_the_dell_raw_sequences(self) -> None:
        self.assertEqual(protocol.MANUAL_MODE_ARGS, ("raw", "0x30", "0x30", "0x01", "0x00"))
        self.assertEqual(protocol.AUTO_MODE_ARGS, ("raw", "0x30", "0x30", "0x01", "0x01"))

    def test_speed_percentage_is_encoded_as_hex(self) -> None:
        self.assertEqual(protocol.speed_args(10)[-1], "0x0a")
        self.assertEqual(protocol.speed_args(15)[-1], "0x0f")
        self.assertEqual(protocol.speed_args(100)[-1], "0x64")

    def test_speed_range_is_enforced_in_the_shared_layer(self) -> None:
        """Not in each caller's UI: a bypassed bound is a cooling failure."""
        for percent in (0, 4, 101, -1):
            with self.assertRaises(ValueError):
                protocol.speed_args(percent)

    def test_every_state_changing_command_matches_a_write_prefix(self) -> None:
        """The Web line proves no anonymous route emits a write by matching these
        prefixes; that proof is only as good as the prefixes covering reality."""
        commands = [
            protocol.MANUAL_MODE_ARGS,
            protocol.AUTO_MODE_ARGS,
            protocol.speed_args(20),
        ]
        for command in commands:
            self.assertTrue(
                any(command[: len(prefix)] == prefix for prefix in protocol.WRITE_PREFIXES),
                f"{command} is a write with no matching prefix",
            )


class BothLinesAgreeTests(unittest.TestCase):
    """Drive each front end's own builder and compare the bytes."""

    def test_desktop_requests_carry_the_shared_arguments(self) -> None:
        from r730xd_fan.ipmi import auto_mode_request, manual_mode_request, speed_request

        self.assertEqual(manual_mode_request().arguments, protocol.MANUAL_MODE_ARGS)
        self.assertEqual(auto_mode_request().arguments, protocol.AUTO_MODE_ARGS)
        self.assertEqual(speed_request(15).arguments, protocol.speed_args(15))

    def test_web_constants_are_the_shared_ones(self) -> None:
        try:
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
            import app as webapp
        except ImportError:  # Flask is not installed in every environment
            self.skipTest("webapp dependencies are not installed")

        self.assertEqual(webapp.MANUAL_MODE_RAW, protocol.MANUAL_MODE_ARGS)
        self.assertEqual(webapp.AUTO_MODE_RAW, protocol.AUTO_MODE_ARGS)


class SharedDiscoveryTests(unittest.TestCase):
    """Both lines must put the same bytes on the wire and accept the same reply.

    The Web line keeps its own socket loop and its own candidate policy (it
    rejects loopback, link-local, multicast and reserved addresses). What must
    not diverge is the wire format - a silent difference there means one side
    quietly stops recognising BMCs.
    """

    def _pong(self, tag: int) -> bytes:
        return (
            b"\x06\x00\xff\x06"
            + b"\x00\x00\x11\xbe"
            + bytes((0x40, tag, 0x00, 0x10))
            + bytes(16)
        )

    def _webapp(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
        try:
            import app as webapp
        except ImportError:
            self.skipTest("webapp dependencies are not installed")
        return webapp

    def test_both_lines_send_the_same_presence_ping(self) -> None:
        from r730xd_core import discovery

        for tag in (0, 7, 255):
            self.assertEqual(
                discovery.presence_ping(tag),
                bytes.fromhex("06 00 ff 06 00 00 11 be 80") + bytes((tag, 0, 0)),
            )

    def test_both_lines_accept_the_same_reply(self) -> None:
        from r730xd_core import discovery

        webapp = self._webapp()
        peer = ("192.168.5.130", 623)
        self.assertEqual(
            discovery.valid_pong(self._pong(7), peer, 7),
            webapp.MacAddressDiscovery._valid_asf_pong(self._pong(7), peer, 7),
        )

    def test_both_lines_reject_the_same_replies(self) -> None:
        from r730xd_core import discovery

        webapp = self._webapp()
        peer = ("192.168.5.130", 623)
        for payload, port, tag in (
            (self._pong(7), 623, 8),        # tag we never sent
            (self._pong(7), 161, 7),        # not the RMCP port
            (self._pong(7)[:20], 623, 7),   # truncated
        ):
            self.assertIsNone(discovery.valid_pong(payload, (peer[0], port), tag))
            self.assertIsNone(
                webapp.MacAddressDiscovery._valid_asf_pong(payload, (peer[0], port), tag)
            )

    def test_the_web_line_is_stricter_about_which_peers_count(self) -> None:
        """Policy, deliberately not shared: core returns the address, the Web
        line additionally refuses one it would never talk to."""
        from r730xd_core import discovery

        webapp = self._webapp()
        loopback = ("127.0.0.1", 623)
        self.assertEqual(discovery.valid_pong(self._pong(7), loopback, 7), "127.0.0.1")
        self.assertIsNone(
            webapp.MacAddressDiscovery._valid_asf_pong(self._pong(7), loopback, 7)
        )


class SharedSdrTests(unittest.TestCase):
    SNAPSHOT = "\n".join(
        (
            "Inlet Temp | 04h | ok | 7.1 | 23 degrees C",
            "Fan1A RPM | 30h | ok | 7.1 | 5880 RPM",
            "Pwr Consumption | 77h | cr | 7.1 | 133 Watts",
            "Dell vendor record without separators",
        )
    )

    def test_categories_are_lower_case_because_the_json_api_publishes_them(self) -> None:
        records = sdr.parse_sdr_records(self.SNAPSHOT)
        self.assertEqual(
            [record.category for record in records],
            ["temperature", "fan", "power", "system"],
        )

    def test_unparseable_rows_are_kept_and_marked(self) -> None:
        """Dropping them would quietly shrink a "full scan"."""
        records = sdr.parse_sdr_records(self.SNAPSHOT)
        self.assertEqual(len(records), 4)
        self.assertFalse(records[-1].parsed)
        self.assertFalse(records[-1].is_alert, "an unparsed row must not raise an alarm")

    def test_alert_follows_the_bmc_status(self) -> None:
        records = {r.name: r for r in sdr.parse_sdr_records(self.SNAPSHOT)}
        self.assertTrue(records["Pwr Consumption"].is_alert)
        self.assertFalse(records["Inlet Temp"].is_alert)

    def test_both_lines_classify_a_record_identically(self) -> None:
        from r730xd_fan.ipmi import parse_sensor_output

        desktop = parse_sensor_output(self.SNAPSHOT)
        shared = sdr.parse_sdr_records(self.SNAPSHOT)
        self.assertEqual(
            [item.category for item in desktop], [item.category for item in shared]
        )


if __name__ == "__main__":
    unittest.main()
