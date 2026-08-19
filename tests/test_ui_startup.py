from __future__ import annotations

import os
import unittest
from unittest.mock import patch


def ok_result(request, stdout=""):
    from r730xd_fan.ipmi import CommandResult

    return CommandResult(
        request=request, returncode=0, stdout=stdout, stderr="", elapsed_seconds=0.5
    )


def sync_console(runner, **kwargs):
    """A window whose commands complete inline, so tests need no threads."""
    from r730xd_fan.ui import FanConsole

    return FanConsole(
        runner=runner,
        spawn=lambda work: work(),
        post=lambda fn: fn(),
        **kwargs,
    )


@unittest.skipUnless(os.name == "nt", "GUI smoke test requires Windows")
class UiStartupTests(unittest.TestCase):
    def test_cold_start_does_not_send_ipmi_command(self) -> None:
        from r730xd_fan.ui import FanConsole

        with patch("r730xd_fan.console.execute") as execute:
            app = FanConsole(startup_message="offline startup test")
            try:
                app.withdraw()
                app.update_idletasks()
                self.assertFalse(app.manual_mode)
                self.assertFalse(app.interlock_var.get())
                self.assertNotIn("[SEND", app.log.get("1.0", "end"))
            finally:
                app.destroy()
        execute.assert_not_called()

    def test_primary_window_only_shows_derived_connection_status(self) -> None:
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="privacy test")
        try:
            app.withdraw()
            app.host_var.set("198.51.100.77")
            app.user_var.set("hidden-admin")
            app.password_var.set("do-not-display")
            app.exe_var.set("C:/Dell/ipmitool.exe")
            app._refresh_connection_summary()
            app.update_idletasks()

            self.assertEqual(app.connection_status_label.cget("text"), "就绪")
            self.assertEqual(app.server_chip.cget("text"), "●  iDRAC  就绪")
            primary_text = " ".join(
                (app.connection_status_label.cget("text"), app.server_chip.cget("text"))
            )
            self.assertNotIn("198.51.100.77", primary_text)
            self.assertNotIn("hidden-admin", primary_text)
            self.assertNotIn("PASSWORD", primary_text)
            self.assertNotIn("密码", primary_text)
            self.assertNotIn("do-not-display", primary_text)
        finally:
            app.destroy()

    def test_main_layout_and_gauge_expand_with_window(self) -> None:
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="responsive layout test")
        try:
            app.attributes("-alpha", 0.0)
            app.deiconify()
            app.geometry("900x700")
            app.update()
            small = (app.gauge.canvas.winfo_width(), app.gauge.canvas.winfo_height())

            app.geometry("1500x950")
            app.update()
            large = (app.gauge.canvas.winfo_width(), app.gauge.canvas.winfo_height())

            self.assertGreaterEqual(large[0], small[0])
            self.assertGreaterEqual(large[1], small[1])
            self.assertTrue(large[0] > small[0] or large[1] > small[1])
        finally:
            app.destroy()

    def test_left_panel_scrolls_and_connection_actions_are_compact(self) -> None:
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="scroll test")
        try:
            app.attributes("-alpha", 0.0)
            app.deiconify()
            app.geometry("900x700")
            app.update()

            self.assertLessEqual(app.test_button.cget("height"), 36)
            self.assertLessEqual(app.sensor_button.cget("height"), 36)
            canvas = app.left_scroll._parent_canvas
            scroll_region = canvas.bbox("all")
            self.assertIsNotNone(scroll_region)
            self.assertGreater(scroll_region[3], canvas.winfo_height())
            before = canvas.yview()
            canvas.yview_moveto(1.0)
            app.update_idletasks()
            after = canvas.yview()
            self.assertGreater(after[0], before[0])
        finally:
            app.destroy()

    def test_sensor_window_keeps_full_output_out_of_event_log(self) -> None:
        from r730xd_fan.ui import SensorDialog

        output = "\n".join(
            f"Sensor {index:02d} | {index:02x}h | ok | 7.1 | {20 + index} degrees C"
            for index in range(50)
        )
        app = sync_console(
            lambda _settings, request: ok_result(request, output),
            startup_message="sensor window test",
        )
        dialog = SensorDialog(app)
        try:
            app._request_sensor_snapshot(dialog)
            self.assertIn("共 50 条", dialog.summary_label.cget("text"))
            import customtkinter as ctk

            children = dialog.sensor_list.winfo_children()
            # One row per reading, nothing truncated, plus one category header
            # for the single TEMPERATURE group these fixtures produce.
            rows = [child for child in children if isinstance(child, ctk.CTkFrame)]
            headers = [child for child in children if isinstance(child, ctk.CTkLabel)]
            self.assertEqual(len(rows), 50)
            self.assertEqual(len(headers), 1)
            self.assertIn("温度", headers[0].cget("text"))
            event_log = app.log.get("1.0", "end")
            self.assertIn("结果已显示在传感器窗口", event_log)
            self.assertNotIn("Sensor 49", event_log)
        finally:
            dialog._close()
            app.destroy()

    def test_sensor_window_is_hidden_and_reused(self) -> None:
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="sensor reuse test")
        try:
            with patch.object(app, "_request_sensor_snapshot", return_value=False):
                app._open_sensor_monitor()
                first = app.sensor_dialog
                self.assertIsNotNone(first)
                first._close()
                app._open_sensor_monitor()
                self.assertIs(app.sensor_dialog, first)
        finally:
            app.destroy()

    def test_readings_row_fills_three_temperatures_and_live_power(self) -> None:
        from r730xd_fan.ipmi import CommandResult, sensor_snapshot_request
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="readings row test")
        try:
            app.withdraw()
            result = CommandResult(
                request=sensor_snapshot_request(),
                returncode=0,
                stdout="\n".join(
                    (
                        "Inlet Temp | 04h | ok | 7.1 | 23 degrees C",
                        "Exhaust Temp | 01h | ok | 7.1 | 35 degrees C",
                        "Temp | 0Eh | ok | 3.1 | 48 degrees C",
                        "Fan1A RPM | 30h | ok | 7.1 | 5880 RPM",
                        "Pwr Consumption | 77h | ok | 7.1 | 133 Watts",
                    )
                ),
                stderr="",
                elapsed_seconds=0.8,
            )
            app.apply_sensor_snapshot(result)
            app.update_idletasks()

            values = [card.value_label.cget("text") for card in app.reading_cards]
            self.assertEqual(values, ["23", "35", "48", "133"])
            details = [card.detail_label.cget("text") for card in app.reading_cards]
            self.assertEqual(
                details, ["Inlet Temp", "Exhaust Temp", "Temp", "Pwr Consumption"]
            )
        finally:
            app.destroy()

    def test_missing_sensors_stay_blank_instead_of_borrowing_another_reading(self) -> None:
        from r730xd_fan.ipmi import CommandResult, sensor_snapshot_request
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="absent sensor test")
        try:
            app.withdraw()
            app.apply_sensor_snapshot(
                CommandResult(
                    request=sensor_snapshot_request(),
                    returncode=0,
                    stdout="Fan1A RPM | 30h | ok | 7.1 | 5880 RPM",
                    stderr="",
                    elapsed_seconds=0.2,
                )
            )
            app.update_idletasks()
            self.assertEqual(
                [card.value_label.cget("text") for card in app.reading_cards],
                ["--", "--", "--", "--"],
            )
        finally:
            app.destroy()

    def test_background_poll_reaches_the_readings_row_without_logging(self) -> None:
        """The event log is for operator actions, not for housekeeping."""
        app = sync_console(
            lambda _settings, request: ok_result(
                request, "Inlet Temp | 04h | ok | 7.1 | 21 degrees C"
            ),
            startup_message="quiet poll test",
        )
        try:
            app.withdraw()
            app.host_var.set("198.51.100.9")
            app.user_var.set("root")
            app.password_var.set("secret")
            app.exe_var.set("C:/Dell/ipmitool.exe")

            app._poll_readings()
            app.update_idletasks()

            log = app.log.get("1.0", "end")
            self.assertNotIn("[SEND", log)
            self.assertNotIn("[OK", log)
            self.assertEqual(app.reading_cards[0].value_label.cget("text"), "21")
        finally:
            app.destroy()

    def test_a_takeover_repaints_the_mode_badge_and_unlocks_the_presets(self) -> None:
        """The controller owns the state; this proves the window renders it."""
        app = sync_console(
            lambda _settings, request: ok_result(request),
            startup_message="state rendering test",
        )
        try:
            app.withdraw()
            app.host_var.set("198.51.100.9")
            app.user_var.set("root")
            app.password_var.set("secret")

            self.assertEqual(app.mode_badge.cget("text"), "状态未知")
            self.assertEqual(app.speed_slider.cget("state"), "disabled")

            app.interlock_var.set(True)
            app._interlock_changed()
            app._enable_manual()
            app.update_idletasks()

            self.assertTrue(app.manual_mode)
            self.assertEqual(app.mode_badge.cget("text"), "手动接管")
            self.assertEqual(app.speed_slider.cget("state"), "normal")

            # The way out repaints everything back, interlock included.
            app._restore_auto()
            app.update_idletasks()
            self.assertFalse(app.manual_mode)
            self.assertEqual(app.mode_badge.cget("text"), "自动温控")
            self.assertFalse(app.interlock_var.get())
            self.assertEqual(app.speed_slider.cget("state"), "disabled")
        finally:
            app.destroy()

    def _laid_out(self, app, width: int, height: int):
        """Apply the layout for a size without asking the window to become it.

        A geometry() request is a request: a session that does not honour it
        leaves the window at whatever size it likes, and an assertion about the
        resulting layout then fails for reasons unrelated to the layout code.
        The breakpoint decision itself is a pure function, tested separately in
        test_presenters.py.
        """
        from r730xd_fan import presenters

        key = presenters.layout_for(width, height)
        app._layout_key = key
        app._apply_layout(*key)
        app.update_idletasks()
        return key

    def test_layout_collapses_as_the_window_shrinks(self) -> None:
        """Auto-collapse, asserted structurally rather than by eye."""
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="layout test")
        try:
            app.attributes("-alpha", 0.0)
            app.deiconify()
            # Stop the live handler from re-deciding from the real window size
            # and undoing what this test just applied. Without this the test
            # fights itself, and does so differently depending on whether the
            # session honoured geometry() at all.
            app.unbind("<Configure>")

            self.assertEqual(self._laid_out(app, 1180, 940), (4, True, False))
            cards = [card.grid_info() for card in app.reading_cards]
            self.assertEqual([int(item["row"]) for item in cards], [0, 0, 0, 0])
            self.assertEqual([int(item["column"]) for item in cards], [0, 1, 2, 3])
            self.assertNotEqual(
                app.left_scroll.grid_info()["column"],
                app.right_panel.grid_info()["column"],
                "wide layout should put the two panels side by side",
            )

            self.assertEqual(self._laid_out(app, 900, 800), (2, True, False))
            cards = [card.grid_info() for card in app.reading_cards]
            self.assertEqual([int(item["row"]) for item in cards], [0, 0, 1, 1])
            self.assertEqual([int(item["column"]) for item in cards], [0, 1, 0, 1])

            self.assertEqual(self._laid_out(app, 600, 660), (2, False, True))
            self.assertNotEqual(
                app.left_scroll.grid_info()["row"],
                app.right_panel.grid_info()["row"],
                "narrow layout should stack the two panels",
            )
            self.assertFalse(
                app.log.winfo_ismapped(), "the full log should be hidden when short"
            )
            self.assertTrue(
                app.log_summary.winfo_ismapped(), "a one-line summary should replace it"
            )

            # And back again: collapsing must be reversible, not one-way.
            self.assertEqual(self._laid_out(app, 1180, 940), (4, True, False))
            self.assertTrue(app.log.winfo_ismapped())
        finally:
            app.destroy()

    def test_the_collapsed_log_shows_the_latest_line(self) -> None:
        from r730xd_fan.ui import FanConsole

        app = FanConsole(startup_message="first line")
        try:
            app.withdraw()
            app._append_log("SEND", "后一条")
            self.assertIn("后一条", app.log_summary.cget("text"))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
