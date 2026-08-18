"""Fan console state and command orchestration, with no tkinter anywhere.

This is the desktop counterpart of ``Backend`` in webapp/app.py: it owns the
state machine and decides what may be sent to the BMC, while the view only
renders and forwards clicks. Everything it talks to is injected, so the safety
interlocks can be exercised on any platform - the GUI tests only run on Windows,
these run on the Linux CI runner too.

The invariants this file exists to protect:

* a fixed speed may only be sent when the interlock is released **and** manual
  mode was actually taken over;
* restoring automatic thermal control is never gated by the interlock;
* a background refresh is silent when it works and loud when it fails;
* the iDRAC password never reaches a log line.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from typing import Any, Protocol

from .config import IpmiSettings
from .ipmi import (
    CommandResult,
    IpmiRequest,
    SensorReading,
    auto_mode_request,
    connection_test_request,
    execute,
    manual_mode_request,
    parse_sensor_output,
    redact_and_limit,
    safe_exception,
    sensor_snapshot_request,
    speed_request,
)
from .presenters import MODE_AUTO, MODE_MANUAL, MODE_UNKNOWN

# A full `sdr elist all` is a heavy operation for an iDRAC8 BMC and repeated
# walks are what exhaust its IPMI sessions (D-022 / E-031). A fan console does
# not need sub-minute resolution.
POLL_SECONDS = 60


class ConsoleListener(Protocol):
    """What the view has to provide. Every method is optional in practice."""

    def on_log(self, level: str, message: str) -> None: ...
    def on_state(self) -> None: ...
    def on_readings(self, readings: list[SensorReading], result: CommandResult) -> None: ...


def _run_inline(work: Callable[[], None]) -> None:
    work()


def _thread_spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="ipmi-command", daemon=True).start()


class FanController:
    def __init__(
        self,
        settings: Callable[[], IpmiSettings],
        *,
        runner: Callable[[IpmiSettings, IpmiRequest], CommandResult] = execute,
        spawn: Callable[[Callable[[], None]], None] = _thread_spawn,
        post: Callable[[Callable[[], None]], None] = _run_inline,
        listener: Any | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        # spawn: how to get off the caller's thread. post: how to get back onto
        # the UI thread. Tests pass synchronous versions of both and the whole
        # command path becomes deterministic.
        self._spawn = spawn
        self._post = post
        self._listener = listener

        self.mode = MODE_UNKNOWN
        self.busy = False
        self.interlock_released = False
        self.current_speed = 10
        self.readings: list[SensorReading] = []

    # ---------------------------------------------------------------- events

    def _log(self, level: str, message: str) -> None:
        """The last gate before text becomes a visible log line.

        ipmi.execute already redacts what it returns, but the runner is
        injectable, so the source is no longer the final say. The guarantee
        belongs where the text actually becomes a log line - here.
        """
        listener = self._listener
        if listener is None or not hasattr(listener, "on_log"):
            return
        try:
            password = self._settings().password
        except Exception:
            password = ""
        listener.on_log(level, redact_and_limit(message, password))

    def _changed(self) -> None:
        listener = self._listener
        if listener is not None and hasattr(listener, "on_state"):
            listener.on_state()

    # ------------------------------------------------------------- commands

    def test_connection(self) -> bool:
        return self._submit(connection_test_request())

    def enable_manual(self) -> bool:
        if not self.interlock_released:
            self._log("BLOCK", "请先解除安全联锁，再关闭自动温控。")
            return False
        return self._submit(manual_mode_request(), on_success=self._manual_ok)

    def restore_auto(self) -> bool:
        """Never gated by the interlock. This is the way back out."""
        return self._submit(auto_mode_request(), on_success=self._auto_ok)

    def set_speed(self, percent: int) -> bool:
        if self.mode != MODE_MANUAL:
            self._log("BLOCK", "必须先成功启用手动控制。")
            return False
        if not self.interlock_released:
            self._log("BLOCK", "安全联锁已锁定，未发送调速指令。")
            return False
        return self._submit(
            speed_request(percent),
            on_success=lambda result: self._speed_ok(percent),
        )

    def refresh_sensors(self, *, quiet: bool = False, on_result: Any = None) -> bool:
        def handle(result: CommandResult) -> None:
            self.readings = parse_sensor_output(result.stdout)
            listener = self._listener
            if listener is not None and hasattr(listener, "on_readings"):
                listener.on_readings(self.readings, result)
            if on_result is not None:
                on_result(result)

        return self._submit(
            sensor_snapshot_request(),
            on_success=handle,
            quiet=quiet,
            log_stdout=False,
        )

    def poll_sensors(self) -> bool:
        """Background refresh: skipped rather than queued when anything is busy."""
        if self.busy or not self._settings_ready():
            return False
        return self.refresh_sensors(quiet=True)

    # ------------------------------------------------------- state mutation

    def set_interlock(self, released: bool) -> None:
        self.interlock_released = released
        self._changed()

    def _manual_ok(self, _result: CommandResult) -> None:
        self.mode = MODE_MANUAL

    def _auto_ok(self, _result: CommandResult) -> None:
        self.mode = MODE_AUTO
        self.interlock_released = False

    def _speed_ok(self, percent: int) -> None:
        self.current_speed = percent

    def _settings_ready(self) -> bool:
        try:
            settings = self._settings()
        except Exception:
            return False
        return bool(
            settings.host
            and settings.username
            and settings.password
            and str(settings.executable)
        )

    # ------------------------------------------------------------ machinery

    def _submit(
        self,
        request: IpmiRequest,
        *,
        on_success: Callable[[CommandResult], None] | None = None,
        quiet: bool = False,
        log_stdout: bool = True,
    ) -> bool:
        if self.busy:
            if not quiet:
                self._log("BUSY", "已有命令正在执行，请稍候。")
            return False

        self.busy = True
        self._changed()
        if not quiet:
            self._log("SEND", f"{request.label}  /  {request.safe_to_log}")
        settings = self._settings()
        # Captured here, not re-read later: the failure path must redact against
        # the password the command actually ran with.
        password = settings.password

        def work() -> None:
            try:
                result = self._runner(settings, request)
            except subprocess.TimeoutExpired:
                self._post(
                    lambda: self._failed("连接超时，iDRAC 未在规定时间内响应。")
                )
            except Exception as exc:  # boundary: surface it, never crash the UI
                message = safe_exception(exc, password)
                self._post(lambda: self._failed(message))
            else:
                self._post(lambda: self._finish(result, on_success, quiet, log_stdout))

        self._spawn(work)
        return True

    def _finish(
        self,
        result: CommandResult,
        on_success: Callable[[CommandResult], None] | None,
        quiet: bool,
        log_stdout: bool,
    ) -> None:
        self.busy = False
        try:
            if result.ok:
                if on_success:
                    on_success(result)
                # A background refresh that worked is not news; the event log is
                # for what the operator did. A failure always is - otherwise a
                # dead link looks like slightly stale data.
                if not quiet:
                    if log_stdout:
                        detail = (
                            result.stdout.replace("\n", " · ")
                            if result.stdout
                            else "iDRAC 已接受命令"
                        )
                    else:
                        detail = "结果已显示在传感器窗口"
                    self._log(
                        "OK",
                        f"{result.request.label} / {detail} / {result.elapsed_seconds:.2f}s",
                    )
            else:
                detail = result.stderr or result.stdout or f"exit code {result.returncode}"
                self._log("ERROR", detail.replace("\n", " · "))
        finally:
            self._changed()

    def _failed(self, message: str) -> None:
        self.busy = False
        try:
            self._log("ERROR", message)
        finally:
            self._changed()
