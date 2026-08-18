"""Safety-interlock behaviour, proved without a display.

The GUI tests in test_ui_startup.py can only run on Windows, so before this file
existed the rules that actually matter - you cannot set a fixed speed without
releasing the interlock, you can always get back to automatic control - were
only ever exercised on one platform, through widgets. FanController takes its
collaborators by injection, so they can be checked directly and anywhere.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from r730xd_fan.config import IpmiSettings
from r730xd_fan.console import FanController
from r730xd_fan.ipmi import CommandResult, IpmiRequest
from r730xd_fan.presenters import MODE_AUTO, MODE_MANUAL, MODE_UNKNOWN

PASSWORD = "super-secret-idrac-password"


class RecordingListener:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []
        self.readings_seen: list[list] = []
        self.state_changes = 0

    def on_log(self, level: str, message: str) -> None:
        self.logs.append((level, message))

    def on_state(self) -> None:
        self.state_changes += 1

    def on_readings(self, readings, result) -> None:
        self.readings_seen.append(readings)

    def levels(self) -> list[str]:
        return [level for level, _ in self.logs]

    def text(self) -> str:
        return "\n".join(f"{level} {message}" for level, message in self.logs)


def settings() -> IpmiSettings:
    return IpmiSettings(
        host="198.51.100.20",
        username="root",
        password=PASSWORD,
        executable=Path("ipmitool.exe"),
    )


def ok_result(request: IpmiRequest, stdout: str = "") -> CommandResult:
    return CommandResult(
        request=request, returncode=0, stdout=stdout, stderr="", elapsed_seconds=0.1
    )


def build(runner=None, *, listener=None):
    """A controller whose command path is fully synchronous."""
    calls: list[IpmiRequest] = []

    def default_runner(_settings, request):
        calls.append(request)
        return ok_result(request)

    def recording_runner(_settings, request):
        calls.append(request)
        return runner(_settings, request)

    controller = FanController(
        settings,
        runner=default_runner if runner is None else recording_runner,
        spawn=lambda work: work(),
        post=lambda fn: fn(),
        listener=listener,
    )
    return controller, calls


class ColdStartTests(unittest.TestCase):
    def test_nothing_is_sent_until_asked(self) -> None:
        controller, calls = build()
        self.assertEqual(calls, [])
        self.assertEqual(controller.mode, MODE_UNKNOWN)
        self.assertFalse(controller.interlock_released)
        self.assertFalse(controller.busy)


class InterlockTests(unittest.TestCase):
    def test_manual_takeover_needs_the_interlock(self) -> None:
        listener = RecordingListener()
        controller, calls = build(listener=listener)

        self.assertFalse(controller.enable_manual())
        self.assertEqual(calls, [])
        self.assertEqual(controller.mode, MODE_UNKNOWN)
        self.assertIn("BLOCK", listener.levels())

    def test_fixed_speed_needs_manual_mode(self) -> None:
        listener = RecordingListener()
        controller, calls = build(listener=listener)
        controller.set_interlock(True)

        self.assertFalse(controller.set_speed(20))
        self.assertEqual(calls, [])
        self.assertIn("BLOCK", listener.levels())

    def test_fixed_speed_needs_the_interlock_even_in_manual_mode(self) -> None:
        """Belt and braces: manual mode alone must not be enough."""
        listener = RecordingListener()
        controller, calls = build(listener=listener)
        controller.set_interlock(True)
        controller.enable_manual()
        calls.clear()

        controller.set_interlock(False)
        self.assertFalse(controller.set_speed(20))
        self.assertEqual(calls, [])

    def test_restore_auto_is_never_gated(self) -> None:
        """The way out must work from every state, including a locked interlock."""
        controller, calls = build()
        self.assertFalse(controller.interlock_released)

        self.assertTrue(controller.restore_auto())
        self.assertEqual([call.safe_to_log for call in calls], ["raw 0x30 0x30 0x01 0x01"])
        self.assertEqual(controller.mode, MODE_AUTO)

    def test_restore_auto_relocks_the_interlock(self) -> None:
        controller, _calls = build()
        controller.set_interlock(True)
        controller.enable_manual()

        controller.restore_auto()
        self.assertEqual(controller.mode, MODE_AUTO)
        self.assertFalse(controller.interlock_released)


class HappyPathTests(unittest.TestCase):
    def test_full_takeover_sequence(self) -> None:
        controller, calls = build()
        controller.set_interlock(True)

        self.assertTrue(controller.enable_manual())
        self.assertEqual(controller.mode, MODE_MANUAL)

        self.assertTrue(controller.set_speed(15))
        self.assertEqual(controller.current_speed, 15)
        self.assertEqual(
            [call.safe_to_log for call in calls],
            ["raw 0x30 0x30 0x01 0x00", "raw 0x30 0x30 0x02 0xff 0x0f"],
        )

    def test_a_failed_takeover_does_not_change_the_mode(self) -> None:
        def failing(_settings, request):
            return CommandResult(
                request=request,
                returncode=1,
                stdout="",
                stderr="Activate Session error",
                elapsed_seconds=0.1,
            )

        listener = RecordingListener()
        controller, _calls = build(failing, listener=listener)
        controller.set_interlock(True)

        controller.enable_manual()
        self.assertEqual(controller.mode, MODE_UNKNOWN)
        self.assertIn("ERROR", listener.levels())


class BackgroundPollTests(unittest.TestCase):
    SNAPSHOT = "\n".join(
        (
            "Inlet Temp | 04h | ok | 7.1 | 23 degrees C",
            "Pwr Consumption | 77h | ok | 7.1 | 133 Watts",
        )
    )

    def test_successful_poll_is_silent_but_still_delivers_readings(self) -> None:
        listener = RecordingListener()
        controller, _calls = build(
            lambda _s, request: ok_result(request, self.SNAPSHOT), listener=listener
        )

        self.assertTrue(controller.poll_sensors())
        self.assertEqual(listener.logs, [])
        self.assertEqual(len(listener.readings_seen), 1)
        self.assertEqual(len(controller.readings), 2)

    def test_failing_poll_is_loud(self) -> None:
        """Silence on success, never on failure: a dead link must not read as
        merely stale data."""

        def failing(_settings, request):
            return CommandResult(
                request=request,
                returncode=1,
                stdout="",
                stderr="Address lookup failed",
                elapsed_seconds=0.1,
            )

        listener = RecordingListener()
        controller, _calls = build(failing, listener=listener)

        controller.poll_sensors()
        self.assertEqual(listener.levels(), ["ERROR"])
        self.assertIn("Address lookup failed", listener.text())

    def test_timeout_is_reported_and_clears_busy(self) -> None:
        def timing_out(_settings, _request):
            raise subprocess.TimeoutExpired(cmd="ipmitool", timeout=5)

        listener = RecordingListener()
        controller, _calls = build(timing_out, listener=listener)

        controller.refresh_sensors()
        self.assertFalse(controller.busy)
        self.assertIn("ERROR", listener.levels())

    def test_poll_is_skipped_while_a_user_command_runs(self) -> None:
        listener = RecordingListener()
        controller, calls = build(listener=listener)
        controller.busy = True

        self.assertFalse(controller.poll_sensors())
        self.assertEqual(calls, [])
        self.assertEqual(listener.logs, [], "a skipped poll must not be announced")

    def test_poll_is_skipped_when_the_connection_is_not_configured(self) -> None:
        blank = IpmiSettings(
            host="", username="", password="", executable=Path("ipmitool.exe")
        )
        calls: list[IpmiRequest] = []

        def runner(_settings, request):
            calls.append(request)
            return ok_result(request)

        controller = FanController(
            lambda: blank,
            runner=runner,
            spawn=lambda work: work(),
            post=lambda fn: fn(),
        )
        self.assertFalse(controller.poll_sensors())
        self.assertEqual(calls, [])


class SecretHandlingTests(unittest.TestCase):
    def test_the_password_never_reaches_a_log_line(self) -> None:
        listener = RecordingListener()

        def echoing(_settings, request):
            # Worst case: the tool echoes something back containing the secret.
            return ok_result(request, f"session opened for root/{PASSWORD}")

        controller, _calls = build(echoing, listener=listener)
        controller.set_interlock(True)
        controller.enable_manual()
        controller.set_speed(20)
        controller.test_connection()

        self.assertNotIn(PASSWORD, listener.text())


class BusyTests(unittest.TestCase):
    def test_a_second_user_command_is_refused_and_announced(self) -> None:
        listener = RecordingListener()
        controller, calls = build(listener=listener)
        controller.busy = True

        self.assertFalse(controller.test_connection())
        self.assertEqual(calls, [])
        self.assertIn("BUSY", listener.levels())


if __name__ == "__main__":
    unittest.main()
