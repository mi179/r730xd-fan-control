"""Finding a BMC without being told where it is, and without leaking anything."""

from __future__ import annotations

import unittest

from r730xd_core import discovery

# Two real layouts. Windows uses dashes and three columns; Linux /proc/net/arp
# uses colons and six. One parser has to survive both.
WINDOWS_ARP = """
Interface: 192.168.5.152 --- 0x18
  Internet Address      Physical Address      Type
  192.168.5.1           14-20-04-0e-2c-56     dynamic
  192.168.5.130         D0-94-66-8C-E0-E3     dynamic
  192.168.5.255         ff-ff-ff-ff-ff-ff     static
"""

LINUX_ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.5.1      0x1         0x2         14:20:04:0e:2c:56     *        br-lan
192.168.5.130    0x1         0x2         d0:94:66:8c:e0:e3     *        br-lan
192.168.5.9      0x1         0x0         00:00:00:00:00:00     *        br-lan
"""


class ArpParsingTests(unittest.TestCase):
    def test_both_layouts_yield_the_same_mapping(self) -> None:
        windows = discovery.parse_arp_pairs(WINDOWS_ARP)
        linux = discovery.parse_arp_pairs(LINUX_ARP)
        for table in (windows, linux):
            self.assertEqual(table["d0:94:66:8c:e0:e3"], "192.168.5.130")
            self.assertEqual(table["14:20:04:0e:2c:56"], "192.168.5.1")

    def test_broadcast_and_incomplete_entries_are_dropped(self) -> None:
        """An all-zero MAC is an unresolved entry, not a device."""
        self.assertNotIn("00:00:00:00:00:00", discovery.parse_arp_pairs(LINUX_ARP))
        self.assertNotIn("ff:ff:ff:ff:ff:ff", discovery.parse_arp_pairs(WINDOWS_ARP))

    def test_header_lines_are_ignored(self) -> None:
        """The Windows header carries an IP but no MAC, so it must not match."""
        self.assertEqual(len(discovery.parse_arp_pairs(WINDOWS_ARP)), 2)

    def test_lookup_accepts_either_separator_and_any_case(self) -> None:
        for written in ("d0:94:66:8c:e0:e3", "D0-94-66-8C-E0-E3", " D0:94:66:8C:E0:E3 "):
            self.assertEqual(
                discovery.address_for_mac(WINDOWS_ARP, written), "192.168.5.130"
            )

    def test_unknown_mac_returns_nothing_rather_than_a_guess(self) -> None:
        self.assertIsNone(discovery.address_for_mac(WINDOWS_ARP, "aa:bb:cc:dd:ee:ff"))

    def test_empty_table_is_not_an_error(self) -> None:
        self.assertEqual(discovery.parse_arp_pairs(""), {})
        self.assertIsNone(discovery.address_for_mac("", "d0:94:66:8c:e0:e3"))


class ScanBoundsTests(unittest.TestCase):
    def test_a_too_wide_range_is_refused_rather_than_swept(self) -> None:
        """A mistyped prefix must not turn into a 65k-host sweep."""
        with self.assertRaises(ValueError):
            discovery.probe_rmcp("10.0.0.0/16")

    def test_a_single_host_is_allowed(self) -> None:
        # /32 has no .hosts(), so this would silently probe nothing if wrong.
        self.assertEqual(
            discovery.probe_rmcp("203.0.113.9/32", timeout=0.01), []
        )


class PongValidationTests(unittest.TestCase):
    """A reply only counts if it carries back the tag we generated."""

    def _pong(self, tag: int) -> bytes:
        return (
            b"\x06\x00\xff\x06"
            + b"\x00\x00\x11\xbe"
            + bytes((0x40, tag, 0x00, 0x10))
            + bytes(16)
        )

    def test_matching_tag_is_accepted(self) -> None:
        peer = ("192.168.5.130", 623)
        self.assertEqual(
            discovery._valid_pong(self._pong(7), peer, 7), "192.168.5.130"
        )

    def test_wrong_tag_is_rejected(self) -> None:
        peer = ("192.168.5.130", 623)
        self.assertIsNone(discovery._valid_pong(self._pong(7), peer, 8))

    def test_wrong_port_is_rejected(self) -> None:
        self.assertIsNone(
            discovery._valid_pong(self._pong(7), ("192.168.5.130", 161), 7)
        )

    def test_truncated_payload_is_rejected(self) -> None:
        self.assertIsNone(
            discovery._valid_pong(self._pong(7)[:20], ("192.168.5.130", 623), 7)
        )


class CandidateTests(unittest.TestCase):
    def test_label_survives_a_missing_mac(self) -> None:
        self.assertEqual(discovery.Candidate("192.168.5.130").label, "192.168.5.130")
        self.assertIn(
            "d0:94:66:8c:e0:e3",
            discovery.Candidate("192.168.5.130", "d0:94:66:8c:e0:e3").label,
        )


if __name__ == "__main__":
    unittest.main()
