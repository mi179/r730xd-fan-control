from __future__ import annotations

import os
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == "nt", "GUI smoke test requires Windows")
class UiStartupTests(unittest.TestCase):
    def test_cold_start_does_not_send_ipmi_command(self) -> None:
        from r730xd_fan.ui import FanConsole

        with patch("r730xd_fan.ui.execute") as execute:
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

            self.assertEqual(app.connection_status_label.cget("text"), "READY")
            self.assertEqual(app.server_chip.cget("text"), "●  IDRAC  READY")
            primary_text = " ".join(
                (app.connection_status_label.cget("text"), app.server_chip.cget("text"))
            )
            self.assertNotIn("198.51.100.77", primary_text)
            self.assertNotIn("hidden-admin", primary_text)
            self.assertNotIn("PASSWORD", primary_text)
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
        from r730xd_fan.ipmi import CommandResult, sensor_snapshot_request
        from r730xd_fan.ui import FanConsole, SensorDialog

        app = FanConsole(startup_message="sensor window test")
        dialog = SensorDialog(app)
        output = "\n".join(
            f"Sensor {index:02d} | {index:02x}h | ok | 7.1 | {20 + index} degrees C"
            for index in range(50)
        )
        result = CommandResult(
            request=sensor_snapshot_request(),
            returncode=0,
            stdout=output,
            stderr="",
            elapsed_seconds=1.25,
        )
        try:
            app.busy = True
            app._command_done(result, dialog.show_result, dialog.finish_loading, False)
            self.assertIn("ALL 50", dialog.summary_label.cget("text"))
            self.assertEqual(len(dialog.sensor_list.winfo_children()), 50)
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


if __name__ == "__main__":
    unittest.main()
