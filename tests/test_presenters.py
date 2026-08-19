"""Pure derivation, checked directly. Runs anywhere - no display required."""

from __future__ import annotations

import unittest

from r730xd_fan import presenters
from r730xd_fan.ipmi import parse_sensor_output
from r730xd_fan.presenters import MODE_AUTO, MODE_MANUAL, MODE_UNKNOWN
from r730xd_fan.view import theme


class GaugeThresholdTests(unittest.TestCase):
    """Pinned to the pre-refactor thresholds: <=30 plain, 31-59 amber, >=60 red.

    These boundaries decide when the console starts shouting about a fan
    setting, so a refactor must not be allowed to nudge them.
    """

    def test_boundaries(self) -> None:
        self.assertEqual(
            [presenters.gauge_tone(v) for v in (5, 30, 31, 59, 60, 100)],
            ["neutral", "neutral", "warn", "warn", "alert", "alert"],
        )

    def test_every_tone_maps_to_a_colour(self) -> None:
        for tone in presenters.TONES:
            self.assertRegex(theme.tone_color(tone), r"^#[0-9A-Fa-f]{6}$")
            fill, text = theme.tone_badge(tone)
            self.assertRegex(fill, r"^#[0-9A-Fa-f]{6}$")
            self.assertRegex(text, r"^#[0-9A-Fa-f]{6}$")


class LayoutBreakpointTests(unittest.TestCase):
    """Where the layout changes shape, checked without a window.

    These are logical pixels. winfo_* reports physical ones, and on a scaled
    display comparing the wrong pair pins the layout to its widest form and
    makes collapsing dead code - a bug no screenshot on an unscaled monitor
    would reveal.
    """

    def test_the_three_shapes(self) -> None:
        self.assertEqual(presenters.layout_for(1180, 940), (4, True, False))
        self.assertEqual(presenters.layout_for(900, 800), (2, True, False))
        self.assertEqual(presenters.layout_for(600, 700), (2, False, True))

    def test_width_boundaries(self) -> None:
        self.assertEqual(presenters.layout_for(1080, 900)[:2], (4, True))
        self.assertEqual(presenters.layout_for(1079, 900)[:2], (2, True))
        self.assertEqual(presenters.layout_for(790, 900)[:2], (2, True))
        self.assertEqual(presenters.layout_for(789, 900)[:2], (2, False))

    def test_height_only_affects_the_log(self) -> None:
        self.assertFalse(presenters.layout_for(1180, 760)[2])
        self.assertTrue(presenters.layout_for(1180, 759)[2])
        self.assertEqual(
            presenters.layout_for(1180, 759)[:2], presenters.layout_for(1180, 940)[:2]
        )

    def test_collapsing_is_reversible(self) -> None:
        """Growing back must return the exact wide shape, not a near miss."""
        wide = presenters.layout_for(1180, 940)
        presenters.layout_for(600, 660)
        self.assertEqual(presenters.layout_for(1180, 940), wide)


class ConnectionStatusTests(unittest.TestCase):
    def test_status_is_derived_not_descriptive(self) -> None:
        """The primary window must never surface host, user or secret."""
        for configured in (True, False):
            text, tone = presenters.connection_status(configured)
            chip, _ = presenters.connection_chip(configured)
            self.assertNotIn(".", text)
            self.assertIn(tone, presenters.TONES)
            self.assertIn("iDRAC", chip)

    def test_unconfigured_is_a_warning_not_an_error(self) -> None:
        self.assertEqual(presenters.connection_status(False)[1], "warn")
        self.assertEqual(presenters.connection_status(True)[1], "ok")

    def test_online_overrides_the_configured_status(self) -> None:
        chip, tone = presenters.connection_chip(True, online=True)
        self.assertIn("在线", chip)
        self.assertEqual(tone, "ok")


class ModeAndOutputTests(unittest.TestCase):
    def test_only_manual_takeover_is_coloured(self) -> None:
        self.assertEqual(presenters.mode_badge(MODE_MANUAL)[1], "alert")
        self.assertEqual(presenters.mode_badge(MODE_AUTO)[1], "neutral")
        self.assertEqual(presenters.mode_badge(MODE_UNKNOWN)[1], "neutral")

    def test_unknown_mode_says_so(self) -> None:
        """Not a failure: the tool cannot read the mode back from iDRAC."""
        self.assertEqual(presenters.mode_badge(MODE_UNKNOWN)[0], "状态未知")

    def test_output_status_shows_the_speed_only_when_taken_over(self) -> None:
        self.assertEqual(presenters.output_status(MODE_MANUAL, 15)[0], "已接管 · 15%")
        self.assertEqual(presenters.output_status(MODE_AUTO, 15)[0], "未接管")


class ReadingCardTests(unittest.TestCase):
    def test_only_a_flagged_reading_is_coloured(self) -> None:
        self.assertEqual(presenters.card_health("alert"), ("异常", "alert"))
        self.assertEqual(presenters.card_health("ok"), ("正常", "muted"))
        self.assertEqual(presenters.card_health("unknown"), ("未知", "muted"))


class FilterAndGroupTests(unittest.TestCase):
    SNAPSHOT = "\n".join(
        (
            "Inlet Temp | 04h | ok | 7.1 | 23 degrees C",
            "Fan1A RPM | 30h | ok | 7.1 | 5880 RPM",
            "Pwr Consumption | 77h | ok | 7.1 | 133 Watts",
            "Exhaust Temp | 01h | cr | 7.1 | 71 degrees C",
            "Dell vendor record without separators",
        )
    )

    def setUp(self) -> None:
        self.readings = parse_sensor_output(self.SNAPSHOT)

    def test_search_covers_name_category_reading_and_status(self) -> None:
        for query, expected in (("inlet", 1), ("rpm", 1), ("watt", 1), ("cr", 1)):
            self.assertEqual(
                len(presenters.filter_readings(self.readings, query)), expected, query
            )

    def test_search_is_case_insensitive_and_trimmed(self) -> None:
        self.assertEqual(len(presenters.filter_readings(self.readings, "  INLET  ")), 1)

    def test_alerts_only_keeps_just_the_flagged_rows(self) -> None:
        alerts = presenters.filter_readings(self.readings, alerts_only=True)
        self.assertEqual([item.name for item in alerts], ["Exhaust Temp"])

    def test_groups_are_ordered_and_localised(self) -> None:
        groups = presenters.group_by_category(self.readings)
        self.assertEqual([label for label, _ in groups], ["温度", "风扇", "功耗", "其他"])
        self.assertEqual([len(rows) for _, rows in groups], [2, 1, 1, 1])

    def test_grouping_keeps_the_original_order_inside_a_group(self) -> None:
        groups = dict(presenters.group_by_category(self.readings))
        self.assertEqual(
            [item.name for item in groups["温度"]], ["Inlet Temp", "Exhaust Temp"]
        )

    def test_summary_turns_red_only_when_something_is_flagged(self) -> None:
        text, tone = presenters.sensor_summary(self.readings)
        self.assertEqual(tone, "alert")
        self.assertIn("共 5 条", text)

        calm = presenters.filter_readings(self.readings, "inlet")
        self.assertEqual(presenters.sensor_summary(calm)[1], "ok")

    def test_unparsed_row_is_muted_rather_than_alarming(self) -> None:
        unparsed = [item for item in self.readings if not item.parsed]
        self.assertEqual(len(unparsed), 1)
        self.assertEqual(presenters.reading_tone(unparsed[0]), "muted")

    def test_reading_tone_follows_the_bmc_status(self) -> None:
        by_name = {item.name: item for item in self.readings}
        self.assertEqual(presenters.reading_tone(by_name["Inlet Temp"]), "ok")
        self.assertEqual(presenters.reading_tone(by_name["Exhaust Temp"]), "alert")


class MetaTextTests(unittest.TestCase):
    def test_readings_meta_states_where_the_numbers_came_from(self) -> None:
        text = presenters.readings_meta(8, "01:02:03", 60)
        self.assertIn("01:02:03", text)
        self.assertIn("8", text)
        self.assertIn("60", text)


if __name__ == "__main__":
    unittest.main()
