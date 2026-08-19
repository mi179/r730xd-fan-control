from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from r730xd_core import discovery

from . import presenters
from .config import IpmiSettings
from .console import POLL_SECONDS, FanController
from .ipmi import (
    CommandResult,
    KeyReading,
    SensorReading,
    parse_sensor_output,
    summarize_key_readings,
)
from .view import theme
from .view.theme import COLORS


class ReadingCard(ctk.CTkFrame):
    """One headline number, styled like the .reading article in app.css."""

    def __init__(self, master: ctk.CTkBaseClass, label: str, unit: str) -> None:
        super().__init__(
            master,
            corner_radius=14,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["surface_2"],
        )
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=label,
            anchor="w",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 11),
        ).grid(row=0, column=0, padx=14, pady=(12, 0), sticky="w")

        value_row = ctk.CTkFrame(self, fg_color="transparent")
        value_row.grid(row=1, column=0, padx=14, pady=(2, 0), sticky="w")
        self.value_label = ctk.CTkLabel(
            value_row,
            text="--",
            text_color=COLORS["text"],
            font=("Cascadia Mono", 30, "bold"),
        )
        self.value_label.pack(side="left")
        ctk.CTkLabel(
            value_row,
            text=unit,
            text_color=COLORS["muted"],
            font=("Cascadia Mono", 12),
        ).pack(side="left", padx=(5, 0), pady=(11, 0))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, padx=14, pady=(1, 12), sticky="ew")
        foot.grid_columnconfigure(0, weight=1)
        self.detail_label = ctk.CTkLabel(
            foot,
            text="等待数据",
            anchor="w",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.detail_label.grid(row=0, column=0, sticky="w")
        self.health_label = ctk.CTkLabel(
            foot,
            text="未知",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.health_label.grid(row=0, column=1, sticky="e")

    def update_reading(self, reading: KeyReading) -> None:
        alert = reading.status == "alert"
        self.value_label.configure(
            text=reading.value,
            # Colour carries meaning and nothing else (D-014): a reading only
            # goes red when the BMC itself flags it.
            text_color=COLORS["red"] if alert else COLORS["text"],
        )
        self.detail_label.configure(text=reading.detail)
        self.health_label.configure(
            text={"ok": "正常", "alert": "异常"}.get(reading.status, "未知"),
            text_color=COLORS["red"] if alert else COLORS["muted"],
        )


class FanGauge(ctk.CTkFrame):
    BASE_WIDTH = 254
    BASE_HEIGHT = 210

    def __init__(self, master: ctk.CTkBaseClass, value: int = 10) -> None:
        super().__init__(master, fg_color="transparent")
        self.value = value
        self.canvas = tk.Canvas(
            self,
            width=self.BASE_WIDTH,
            height=self.BASE_HEIGHT,
            bg=COLORS["surface"],
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self._render_gauge()

    def set_value(self, value: int) -> None:
        self.value = max(0, min(100, value))
        self._render_gauge()

    def _canvas_resized(self, event: tk.Event) -> None:
        self._render_gauge(event.width, event.height)

    def _render_gauge(self, width: int | None = None, height: int | None = None) -> None:
        canvas = self.canvas
        canvas.delete("all")
        canvas_width = width or canvas.winfo_width()
        canvas_height = height or canvas.winfo_height()
        if canvas_width <= 1:
            canvas_width = self.BASE_WIDTH
        if canvas_height <= 1:
            canvas_height = self.BASE_HEIGHT

        scale = min(canvas_width / self.BASE_WIDTH, canvas_height / self.BASE_HEIGHT)
        scale = max(0.65, min(1.75, scale))
        offset_x = (canvas_width - self.BASE_WIDTH * scale) / 2
        offset_y = (canvas_height - self.BASE_HEIGHT * scale) / 2

        def point(x: float, y: float) -> tuple[float, float]:
            return offset_x + x * scale, offset_y + y * scale

        x1, y1 = point(34, 20)
        x2, y2 = point(220, 206)
        box = (x1, y1, x2, y2)
        arc_width = max(9, round(15 * scale))
        canvas.create_arc(
            *box,
            start=200,
            extent=140,
            style="arc",
            width=arc_width,
            outline=COLORS["line"],
        )
        # A short arc drawn with a thick butt cap reads as a detached square at
        # low percentages; a rounded cap keeps it looking like the start of a
        # sweep. Zero stays genuinely empty.
        extent = 140 * self.value / 100
        if extent > 0:
            canvas.create_arc(
                *box,
                start=200,
                extent=max(extent, 1.5),
                style="arc",
                width=arc_width,
                outline=theme.tone_color(presenters.gauge_tone(self.value)),
            )
        center_x, value_y = point(127, 103)
        canvas.create_text(
            center_x,
            value_y,
            text=f"{self.value}",
            fill=COLORS["text"],
            font=("Cascadia Mono", max(28, round(44 * scale)), "bold"),
        )
        center_x, label_y = point(127, 143)
        canvas.create_text(
            center_x,
            label_y,
            text="转速百分比",
            fill=COLORS["muted"],
            font=("Microsoft YaHei UI", max(8, round(10 * scale)), "bold"),
        )
        zero_x, marker_y = point(34, 190)
        hundred_x, _ = point(220, 190)
        marker_font = ("Cascadia Mono", max(8, round(10 * scale)))
        canvas.create_text(
            zero_x, marker_y, text="0", fill=COLORS["muted"], anchor="w", font=marker_font
        )
        canvas.create_text(
            hundred_x,
            marker_y,
            text="100",
            fill=COLORS["muted"],
            anchor="e",
            font=marker_font,
        )


class ConnectionDialog(ctk.CTkToplevel):
    def __init__(self, owner: FanConsole) -> None:
        super().__init__(owner, fg_color=COLORS["background"])
        self.owner = owner
        self.title("iDRAC 连接设置")
        # Taller than the field list needs: the scan button and its hint sit
        # below 显示密码, and clipping them hides the whole point of the dialog.
        self.geometry("580x620")
        self.minsize(500, 580)
        self.resizable(True, True)
        self.transient(owner)

        self.host_var = tk.StringVar(value=owner.host_var.get())
        self.user_var = tk.StringVar(value=owner.user_var.get())
        self.password_var = tk.StringVar(value=owner.password_var.get())
        self.exe_var = tk.StringVar(value=owner.exe_var.get())
        self.show_password_var = tk.BooleanVar(value=False)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, corner_radius=0, fg_color=COLORS["surface"])
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header,
            text="iDRAC 连接设置",
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))
        ctk.CTkLabel(
            header,
            text="密码仅保存在当前程序内存，不会写入配置文件",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", padx=24, pady=(0, 18))

        form = ctk.CTkFrame(
            self,
            corner_radius=12,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["surface"],
        )
        form.grid(row=1, column=0, padx=22, pady=20, sticky="nsew")
        form.grid_columnconfigure((0, 1), weight=1)

        self._add_entry(form, "iDRAC 地址", self.host_var, row=0, column=0)
        self._add_entry(form, "用户名", self.user_var, row=0, column=1)
        self.password_entry = self._add_entry(
            form, "密码", self.password_var, row=1, column=0, columnspan=2, show="●"
        )
        self._add_entry(
            form, "ipmitool 路径", self.exe_var, row=2, column=0, columnspan=2
        )

        show_password = ctk.CTkCheckBox(
            form,
            text="显示密码",
            variable=self.show_password_var,
            command=self._toggle_password,
            checkbox_width=17,
            checkbox_height=17,
            border_width=1,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        )
        show_password.grid(row=3, column=0, columnspan=2, padx=18, pady=(0, 6), sticky="w")

        self.scan_button = ctk.CTkButton(
            form,
            text="扫描局域网找 iDRAC",
            height=34,
            corner_radius=10,
            fg_color=COLORS["surface_2"],
            hover_color=COLORS["line"],
            border_width=1,
            border_color=COLORS["line"],
            font=("Microsoft YaHei UI", 10, "bold"),
            command=self._scan,
        )
        self.scan_button.grid(row=4, column=0, columnspan=2, padx=18, pady=(0, 6), sticky="ew")
        self.scan_hint = ctk.CTkLabel(
            form,
            text="不知道地址就点这里；只发不带密码的探测包",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.scan_hint.grid(row=5, column=0, columnspan=2, padx=18, pady=(0, 14), sticky="w")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, padx=22, pady=(0, 22), sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            actions,
            text="取消",
            height=42,
            corner_radius=10,
            fg_color=COLORS["surface_2"],
            hover_color=COLORS["line"],
            border_width=1,
            border_color=COLORS["line"],
            font=("Microsoft YaHei UI", 11, "bold"),
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(
            actions,
            text="保存设置",
            height=42,
            corner_radius=10,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            font=("Microsoft YaHei UI", 11, "bold"),
            command=self._save,
        ).grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.after(80, self._activate)

    def _add_entry(
        self,
        parent: ctk.CTkFrame,
        label: str,
        variable: tk.StringVar,
        *,
        row: int,
        column: int,
        columnspan: int = 1,
        show: str | None = None,
    ) -> ctk.CTkEntry:
        field = ctk.CTkFrame(parent, fg_color="transparent")
        field.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            padx=18,
            pady=(14, 6),
            sticky="ew",
        )
        ctk.CTkLabel(
            field,
            text=label,
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        entry = ctk.CTkEntry(
            field,
            textvariable=variable,
            show=show,
            height=38,
            corner_radius=7,
            fg_color=COLORS["background"],
            border_color=COLORS["line"],
            text_color=COLORS["text"],
            font=("Cascadia Mono", 11),
        )
        entry.pack(fill="x")
        return entry

    def _scan(self) -> None:
        """Find BMCs on this segment. Runs off the UI thread; ~2 s of waiting."""
        self.scan_button.configure(state="disabled", text="扫描中…")
        self.scan_hint.configure(text="正在查网段…", text_color=COLORS["muted"])

        def work() -> None:
            try:
                # scan_range() shells out (PowerShell on Windows, up to 15 s),
                # so it belongs here rather than before the thread starts -
                # otherwise pressing the button freezes the window.
                scan = discovery.scan_range()
                if scan is None:
                    self.after(0, lambda: self._scan_failed("无法判断本机所在网段"))
                    return
                hint = f"正在探测 {scan.network}…"
                if scan.note:
                    hint = f"{hint}（{scan.note}）"
                self.after(
                    0,
                    lambda text=hint: self.scan_hint.configure(
                        text=text, text_color=COLORS["muted"]
                    ),
                )
                # No ARP table is passed in: before the probe runs, the machine
                # has never spoken to these hosts and the table cannot know
                # them. The probe is what creates the entries, so it is read
                # afterwards.
                found = discovery.discover(scan.network, timeout=2.5)
                arp = discovery.parse_arp_pairs(discovery.read_arp_table())
                by_address = {address: mac for mac, address in arp.items()}
                resolved = [
                    discovery.Candidate(item.address, by_address.get(item.address))
                    for item in found
                ]
            except Exception as exc:  # boundary: a scan must never kill the dialog
                message = str(exc)
                self.after(0, lambda: self._scan_failed(message))
            else:
                self.after(0, lambda: self._scan_done(resolved))

        threading.Thread(target=work, name="idrac-scan", daemon=True).start()

    def _scan_failed(self, message: str) -> None:
        self.scan_button.configure(state="normal", text="扫描局域网找 iDRAC")
        self.scan_hint.configure(text=message, text_color=COLORS["amber"])

    def _scan_done(self, candidates: list) -> None:
        self.scan_button.configure(state="normal", text="扫描局域网找 iDRAC")
        if not candidates:
            self.scan_hint.configure(
                text="没有设备应答 IPMI；确认 iDRAC 已启用 IPMI over LAN",
                text_color=COLORS["amber"],
            )
            return
        chosen = candidates[0]
        self.host_var.set(chosen.address)
        if chosen.mac:
            self.owner.remembered_mac = chosen.mac
        extra = f"；还发现 {len(candidates) - 1} 台，如需其它请手填" if len(candidates) > 1 else ""
        self.scan_hint.configure(
            text=f"找到 {chosen.label}{extra}", text_color=COLORS["ok"]
        )

    def _activate(self) -> None:
        self.grab_set()
        self.focus_force()
        self.password_entry.focus_set()

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "●")

    def _save(self) -> None:
        self.owner.host_var.set(self.host_var.get().strip())
        self.owner.user_var.set(self.user_var.get().strip())
        self.owner.password_var.set(self.password_var.get())
        self.owner.exe_var.set(self.exe_var.get().strip())
        self.owner._refresh_connection_summary()
        self.destroy()


class SensorDialog(ctk.CTkToplevel):
    def __init__(self, owner: FanConsole) -> None:
        super().__init__(owner, fg_color=COLORS["background"])
        self.owner = owner
        self.title("完整传感器扫描")
        self.geometry("920x640")
        self.minsize(650, 450)
        self.transient(owner)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(self, corner_radius=0, fg_color=COLORS["surface"])
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=24, pady=17, sticky="w")
        ctk.CTkLabel(
            title_box,
            text="完整传感器扫描",
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="温度 · 风扇 RPM · 功耗 · 电压 · 全部 SDR 记录",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        self.refresh_button = ctk.CTkButton(
            header,
            text="刷新",
            width=108,
            height=30,
            corner_radius=8,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            font=("Microsoft YaHei UI", 10, "bold"),
            command=self.refresh,
        )
        self.refresh_button.grid(row=0, column=1, padx=24, pady=20, sticky="e")

        summary = ctk.CTkFrame(self, fg_color="transparent")
        summary.grid(row=1, column=0, padx=22, pady=(14, 8), sticky="ew")
        summary.grid_columnconfigure(0, weight=1)
        self.summary_label = ctk.CTkLabel(
            summary,
            text="等待传感器数据",
            text_color=COLORS["reading"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.summary_label.grid(row=0, column=0, sticky="w")
        self.updated_label = ctk.CTkLabel(
            summary,
            text="",
            text_color=COLORS["muted"],
            font=("Cascadia Mono", 9),
        )
        self.updated_label.grid(row=0, column=1, sticky="e")

        filters = ctk.CTkFrame(self, fg_color="transparent")
        filters.grid(row=2, column=0, padx=22, pady=(0, 10), sticky="ew")
        filters.grid_columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filters())
        self.search_entry = ctk.CTkEntry(
            filters,
            textvariable=self.search_var,
            placeholder_text="搜索名称、类型、读数或状态",
            height=32,
            corner_radius=8,
            fg_color=COLORS["surface"],
            border_color=COLORS["line"],
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.alerts_only = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            filters,
            text="只看异常",
            variable=self.alerts_only,
            command=self._apply_filters,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=5,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            border_color=COLORS["line"],
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).grid(row=0, column=1, padx=(12, 0))

        self.sensor_list = ctk.CTkScrollableFrame(
            self,
            corner_radius=12,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["surface"],
            scrollbar_fg_color=COLORS["surface"],
            scrollbar_button_color=COLORS["line"],
            scrollbar_button_hover_color=COLORS["muted"],
        )
        self.sensor_list.grid(row=3, column=0, padx=22, pady=(0, 20), sticky="nsew")
        # Keep the last snapshot so filtering never re-queries the iDRAC.
        self._readings: list[SensorReading] = []
        self._render_empty("正在读取 iDRAC 传感器……")
        self._initial_refresh_job = self.after(120, self._initial_refresh)

    def _initial_refresh(self) -> None:
        self._initial_refresh_job = None
        self.refresh()

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def refresh(self) -> None:
        if not self._alive():
            return
        if self._initial_refresh_job is not None:
            self.after_cancel(self._initial_refresh_job)
            self._initial_refresh_job = None
        self.set_loading()
        if not self.owner._request_sensor_snapshot(self):
            self.finish_loading()

    def set_loading(self) -> None:
        if not self._alive():
            return
        self.refresh_button.configure(state="disabled", text="读取中…")
        self.updated_label.configure(text="正在读取 SDR")

    def finish_loading(self) -> None:
        if self._alive():
            self.refresh_button.configure(state="normal", text="刷新")

    def show_result(self, result: CommandResult) -> None:
        if not self._alive():
            return
        readings = parse_sensor_output(result.stdout)
        self._readings = readings
        self._render_readings(self._visible_readings())

        summary_text, tone = presenters.sensor_summary(readings)
        self.summary_label.configure(text=summary_text, text_color=theme.tone_color(tone))
        self.updated_label.configure(
            text=presenters.scan_timing(
                datetime.now().strftime("%H:%M:%S"), result.elapsed_seconds
            )
        )
        self.owner._append_log("SENSOR", presenters.sensor_log_line(readings))

    def _visible_readings(self) -> list[SensorReading]:
        return presenters.filter_readings(
            self._readings,
            self.search_var.get(),
            alerts_only=bool(self.alerts_only.get()),
        )

    def _apply_filters(self) -> None:
        if not self._alive():
            return
        visible = self._visible_readings()
        if self._readings and not visible:
            self._render_empty("没有匹配的传感器记录。")
            return
        self._render_readings(visible)

    def _render_readings(self, readings: list[SensorReading]) -> None:
        for child in self.sensor_list.winfo_children():
            child.destroy()
        if not readings:
            self._render_empty("iDRAC 没有返回可显示的传感器记录。")
            return

        row_index = -1
        for label, group in presenters.group_by_category(readings):
            ctk.CTkLabel(
                self.sensor_list,
                text=f"{label}  ({len(group)})",
                anchor="w",
                text_color=COLORS["text"],
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(
                fill="x",
                padx=8,
                pady=(14 if row_index >= 0 else 6, 4),
                anchor="w",
            )
            for reading in group:
                row_index += 1
                self._render_row(reading, row_index)

    def _render_row(self, reading: SensorReading, row_index: int) -> None:
        row = ctk.CTkFrame(
            self.sensor_list,
            corner_radius=8,
            fg_color=COLORS["surface_2"] if row_index % 2 == 0 else COLORS["surface"],
        )
        row.pack(fill="x", padx=3, pady=2)
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)

        name_box = ctk.CTkFrame(row, fg_color="transparent")
        name_box.grid(row=0, column=0, padx=12, pady=8, sticky="ew")
        ctk.CTkLabel(
            name_box,
            text=reading.name,
            anchor="w",
            justify="left",
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill="x", anchor="w")
        metadata = [reading.category]
        if reading.sensor_id:
            metadata.append(f"ID {reading.sensor_id}")
        if reading.entity:
            metadata.append(f"ENTITY {reading.entity}")
        ctk.CTkLabel(
            name_box,
            text="  ·  ".join(metadata),
            anchor="w",
            text_color=COLORS["muted"],
            font=("Cascadia Mono", 8),
        ).pack(fill="x", anchor="w", pady=(2, 0))

        ctk.CTkLabel(
            row,
            text=reading.reading or "—",
            anchor="w",
            justify="left",
            text_color=COLORS["reading"],
            font=("Cascadia Mono", 9, "bold"),
        ).grid(row=0, column=1, padx=10, pady=8, sticky="ew")

        status_color = theme.tone_color(presenters.reading_tone(reading))
        ctk.CTkLabel(
            row,
            text=reading.status.upper() or "N/A",
            width=72,
            height=28,
            corner_radius=7,
            fg_color=COLORS["background"],
            text_color=status_color,
            font=("Cascadia Mono", 9, "bold"),
        ).grid(row=0, column=2, padx=(6, 12), pady=8, sticky="e")

    def _render_empty(self, message: str) -> None:
        for child in self.sensor_list.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.sensor_list,
            text=message,
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 11),
        ).pack(padx=24, pady=40)

    def _close(self) -> None:
        if self._initial_refresh_job is not None:
            self.after_cancel(self._initial_refresh_job)
            self._initial_refresh_job = None
        # CTkScrollableFrame 6.0 registers global wheel bindings. Reuse this
        # window instead of repeatedly destroying/recreating those bindings.
        self.withdraw()


class FanConsole(ctk.CTk):
    def __init__(
        self,
        startup_message: str | None = None,
        *,
        runner=None,
        spawn=None,
        post=None,
    ) -> None:
        super().__init__(fg_color=COLORS["background"])
        self.title("R730xd 热控控制台")
        # The readings row costs about 150 px of height, and the connection card
        # must stay above the fold: it is the first thing a new user needs.
        self.geometry("1180x940")
        # Low enough that the stacked layout is actually reachable. The old
        # 900x700 floor predated collapsing and silently blocked it.
        self.minsize(560, 520)

        defaults = IpmiSettings.from_environment()
        self.action_buttons: list[ctk.CTkButton] = []
        self.speed_buttons: list[ctk.CTkButton] = []
        self.sensor_dialog: SensorDialog | None = None
        self.reading_cards: list[ReadingCard] = []
        self._poll_job: str | None = None
        self._pending_dialog: SensorDialog | None = None
        # Only re-sync the slider when the speed actually changed, so a state
        # update mid-command cannot snap a slider the user is dragging.
        self._shown_speed: int | None = None
        self._layout_key: tuple | None = None
        # IP is a lease, MAC is the identity. Learned from a scan, then used
        # to re-locate the same BMC after DHCP moves it.
        self.remembered_mac: str | None = None

        self.host_var = tk.StringVar(value=defaults.host)
        self.user_var = tk.StringVar(value=defaults.username)
        self.password_var = tk.StringVar(value=defaults.password)
        self.exe_var = tk.StringVar(value=str(defaults.executable))
        self.interlock_var = tk.BooleanVar(value=False)

        # All state and command sequencing lives in the controller; this class
        # only renders it and forwards clicks. `post` is how a worker thread
        # gets back onto the Tk thread. Injectable so tests can drive the whole
        # path synchronously - the same reason create_app takes its runner.
        # The controller needs self._settings and this window needs the
        # controller, so the pieces are injected rather than the whole object.
        overrides = {
            name: value
            for name, value in (("runner", runner), ("spawn", spawn))
            if value is not None
        }
        self.controller = FanController(
            self._settings,
            post=post or (lambda fn: self.after(0, fn)),
            listener=self,
            **overrides,
        )
        # Bound after the controller exists. A trace rather than only the
        # switch's command, so a programmatic set cannot leave the two out of
        # step - which is exactly what the screenshot diff caught.
        self.interlock_var.trace_add("write", self._interlock_changed)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()
        self._build_log_panel()
        self._refresh_connection_summary()
        self._append_log("SYSTEM", startup_message or "控制台就绪。先测试连接，再解除安全联锁。")
        self._update_controls()
        self._schedule_poll(first=True)
        self.bind("<Configure>", self._on_resize)
        self.after(60, self._on_resize)

    # ------------------------------------------------------------------
    # Controller state, exposed read-only for the view, tests and preview.

    @property
    def busy(self) -> bool:
        return self.controller.busy

    @busy.setter
    def busy(self, value: bool) -> None:
        self.controller.busy = value

    @property
    def manual_mode(self) -> bool:
        return self.controller.mode == presenters.MODE_MANUAL

    @property
    def current_speed(self) -> int:
        return self.controller.current_speed

    # ------------------------------------------------------------------
    # ConsoleListener: the controller pushes, the view renders.

    def on_log(self, level: str, message: str) -> None:
        self._append_log(level, message)

    def on_readings(self, readings: list[SensorReading], _result: CommandResult) -> None:
        self._render_reading_cards(readings)

    def on_state(self) -> None:
        """Single place where controller state becomes widget state."""
        controller = self.controller
        if self.interlock_var.get() != controller.interlock_released:
            self.interlock_var.set(controller.interlock_released)

        text, tone = presenters.mode_badge(controller.mode)
        badge_fill, badge_text = theme.tone_badge(tone)
        self.mode_badge.configure(text=text, fg_color=badge_fill, text_color=badge_text)

        if controller.current_speed != self._shown_speed:
            self._shown_speed = controller.current_speed
            self.custom_value.set(controller.current_speed)
            self.slider_value.configure(
                text=presenters.custom_speed_label(controller.current_speed)
            )
            self.gauge.set_value(controller.current_speed)

        if self._pending_dialog is not None and not controller.busy:
            self._pending_dialog = None

        self._update_controls()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, height=92, corner_radius=0, fg_color=COLORS["surface"])
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=28, pady=18, sticky="w")
        ctk.CTkLabel(
            title_box,
            text="R730XD 热控",
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 22, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="Dell PowerEdge · iDRAC 风扇接管控制台",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 11),
        ).pack(anchor="w", pady=(4, 0))

        self.server_chip = ctk.CTkLabel(
            header,
            text="●  iDRAC  需要配置",
            height=36,
            corner_radius=18,
            fg_color=COLORS["surface_2"],
            text_color=COLORS["reading"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.server_chip.grid(row=0, column=1, padx=28, pady=25, sticky="e")

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=22, pady=18, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=280)
        body.grid_columnconfigure(1, weight=7, minsize=0)
        body.grid_rowconfigure(1, weight=1)

        self._build_readings_row(body)

        self.left_scroll = ctk.CTkScrollableFrame(
            body,
            corner_radius=14,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["surface"],
            scrollbar_fg_color=COLORS["surface"],
            scrollbar_button_color=COLORS["line"],
            scrollbar_button_hover_color=COLORS["muted"],
            orientation="vertical",
        )
        self.left_scroll.grid(row=1, column=0, padx=(0, 14), sticky="nsew")
        self.body = body
        self._build_mode_card(self.left_scroll)
        self._build_connection_card(self.left_scroll)

        right = ctk.CTkFrame(
            body,
            corner_radius=14,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["surface"],
        )
        right.grid(row=1, column=1, sticky="nsew")
        self.right_panel = right
        self._build_output_card(right)

    def _build_readings_row(self, body: ctk.CTkFrame) -> None:
        """The four headline numbers: three temperatures plus live power.

        Same four cards as the Web console's readings row. The Web-only trend
        chart is deliberately absent — the desktop keeps no sample history.
        """
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=0, column=0, columnspan=2, pady=(0, 14), sticky="ew")
        self.readings_row = row

        for label, unit in (
            ("进风温度", "°C"),
            ("排风温度", "°C"),
            ("CPU 温度", "°C"),
            ("实时功耗", "W"),
        ):
            self.reading_cards.append(ReadingCard(row, label, unit))

        self.readings_meta = ctk.CTkLabel(
            row,
            text="等待数据",
            anchor="w",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )

    def apply_sensor_snapshot(self, result: CommandResult) -> None:
        """Feed the readings row from a raw `sdr elist all` result."""
        self._render_reading_cards(parse_sensor_output(result.stdout))

    def _render_reading_cards(self, readings: list[SensorReading]) -> None:
        cards = summarize_key_readings(readings)
        for card, reading in zip(self.reading_cards, cards, strict=True):
            card.update_reading(reading)
        self.readings_meta.configure(
            text=presenters.readings_meta(
                len(readings), datetime.now().strftime("%H:%M:%S"), POLL_SECONDS
            )
        )

    def _schedule_poll(self, *, first: bool = False) -> None:
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
        delay = 3000 if first else POLL_SECONDS * 1000
        self._poll_job = self.after(delay, self._poll_readings)

    def _poll_readings(self) -> None:
        self._poll_job = None
        self.controller.poll_sensors()
        self._schedule_poll()

    # ------------------------------------------------------------------
    # Auto-collapsing layout. The window reflows itself; there is no manual
    # toggle, because a fan console that needs to be told it is small is a fan
    # console you have to think about.

    def _on_resize(self, _event=None) -> None:
        # winfo_* reports physical pixels, but the breakpoints below are written
        # in the same logical units as geometry() and every widget size in this
        # file. On a 150% display the two differ by half again, which silently
        # pins the layout to its widest form and makes collapsing dead code.
        try:
            scaling = ctk.ScalingTracker.get_window_scaling(self) or 1.0
        except Exception:
            scaling = 1.0
        width = round(self.winfo_width() / scaling)
        height = round(self.winfo_height() / scaling)
        if width <= 1 or height <= 1:
            return
        key = presenters.layout_for(width, height)
        columns, side_by_side, compact_log = key
        # Configure fires constantly during a drag; only act on a real change.
        if key == self._layout_key:
            return
        self._layout_key = key
        self._apply_layout(columns, side_by_side, compact_log)

    def _apply_layout(self, columns: int, side_by_side: bool, compact_log: bool) -> None:
        row = self.readings_row
        for index in range(4):
            row.grid_columnconfigure(index, weight=0, uniform="")
        for index in range(columns):
            row.grid_columnconfigure(index, weight=1, uniform="readings")

        for index, card in enumerate(self.reading_cards):
            grid_row, grid_column = divmod(index, columns)
            first = grid_column == 0
            last = grid_column == columns - 1
            card.grid(
                row=grid_row,
                column=grid_column,
                padx=(0 if first else 7, 0 if last else 7),
                pady=(0 if grid_row == 0 else 10, 0),
                sticky="ew",
            )
        meta_row = (len(self.reading_cards) + columns - 1) // columns
        self.readings_meta.grid(
            row=meta_row, column=0, columnspan=columns, pady=(7, 0), sticky="w"
        )

        body = self.body
        if side_by_side:
            body.grid_columnconfigure(0, weight=3, minsize=280)
            body.grid_columnconfigure(1, weight=7, minsize=0)
            body.grid_rowconfigure(1, weight=1)
            body.grid_rowconfigure(2, weight=0)
            self.left_scroll.grid(row=1, column=0, columnspan=1, padx=(0, 14), sticky="nsew")
            self.right_panel.grid(row=1, column=1, columnspan=1, pady=0, sticky="nsew")
        else:
            # Too narrow to stand side by side: stack, and let the fan output
            # card take the growth because it holds the gauge.
            body.grid_columnconfigure(0, weight=1, minsize=0)
            body.grid_columnconfigure(1, weight=0, minsize=0)
            body.grid_rowconfigure(1, weight=0)
            body.grid_rowconfigure(2, weight=1)
            self.left_scroll.grid(row=1, column=0, columnspan=2, padx=0, sticky="ew")
            self.right_panel.grid(row=2, column=0, columnspan=2, pady=(14, 0), sticky="nsew")

        if compact_log:
            self.log.grid_remove()
            self.log_summary.grid()
            self.log_panel.configure(height=64)
        else:
            self.log_summary.grid_remove()
            self.log.grid()
            self.log_panel.configure(height=128)

    def _section_label(self, master: ctk.CTkBaseClass, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            master,
            text=text,
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def _build_mode_card(self, parent: ctk.CTkFrame) -> None:
        section = ctk.CTkFrame(parent, fg_color="transparent")
        section.pack(fill="x", padx=20, pady=(18, 10))
        self._section_label(section, "01 / 控制模式").pack(anchor="w")

        mode_row = ctk.CTkFrame(section, fg_color="transparent")
        mode_row.pack(fill="x", pady=(10, 10))
        self.mode_badge = ctk.CTkLabel(
            mode_row,
            text="状态未知",
            height=34,
            corner_radius=8,
            fg_color=COLORS["surface_2"],
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.mode_badge.pack(fill="x")

        self.interlock_switch = ctk.CTkSwitch(
            section,
            text="解除安全联锁",
            variable=self.interlock_var,
            command=self._interlock_changed,
            progress_color=COLORS["amber"],
            button_color=COLORS["reading"],
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.interlock_switch.pack(fill="x", pady=(2, 12))

        self.manual_button = ctk.CTkButton(
            section,
            text="接管风扇控制",
            height=42,
            corner_radius=10,
            fg_color=COLORS["red"],
            hover_color=COLORS["red_hover"],
            font=("Microsoft YaHei UI", 12, "bold"),
            command=self._enable_manual,
        )
        self.manual_button.pack(fill="x", pady=(0, 8))
        self.action_buttons.append(self.manual_button)

        self.auto_button = ctk.CTkButton(
            section,
            text="恢复自动温控",
            height=38,
            corner_radius=10,
            fg_color=COLORS["surface_2"],
            hover_color=COLORS["line"],
            border_width=1,
            border_color=COLORS["line"],
            font=("Microsoft YaHei UI", 11, "bold"),
            command=self._restore_auto,
        )
        self.auto_button.pack(fill="x")
        self.action_buttons.append(self.auto_button)

        ctk.CTkLabel(
            section,
            text="恢复自动温控始终可用。\n退出程序不会自动改变当前模式。",
            justify="left",
            text_color=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(9, 0))

        ctk.CTkFrame(parent, height=1, fg_color=COLORS["line"]).pack(fill="x", padx=20)

    def _build_connection_card(self, parent: ctk.CTkFrame) -> None:
        section = ctk.CTkFrame(parent, fg_color="transparent")
        section.pack(fill="both", expand=True, padx=20, pady=(12, 16))
        self._section_label(section, "02 / 连接").pack(anchor="w", pady=(0, 10))

        summary = ctk.CTkFrame(
            section,
            corner_radius=8,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["background"],
        )
        summary.pack(fill="x", pady=(0, 10))
        self.connection_status_label = ctk.CTkLabel(
            summary,
            text="需要配置",
            text_color=COLORS["amber"],
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.connection_status_label.pack(fill="x", padx=12, pady=13)

        self.settings_button = ctk.CTkButton(
            section,
            text="连接设置",
            height=38,
            corner_radius=10,
            fg_color=COLORS["surface_2"],
            hover_color=COLORS["line"],
            border_width=1,
            border_color=COLORS["line"],
            font=("Microsoft YaHei UI", 11, "bold"),
            command=self._open_connection_settings,
        )
        self.settings_button.pack(fill="x", pady=(0, 8))
        self.action_buttons.append(self.settings_button)

        quick_actions = ctk.CTkFrame(section, fg_color="transparent")
        quick_actions.pack(fill="x", pady=(2, 0))
        quick_actions.grid_columnconfigure((0, 1), weight=1)

        self.test_button = ctk.CTkButton(
            quick_actions,
            text="测试连接",
            height=32,
            corner_radius=10,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            font=("Microsoft YaHei UI", 10, "bold"),
            command=self._test_connection,
        )
        self.test_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.action_buttons.append(self.test_button)

        self.sensor_button = ctk.CTkButton(
            quick_actions,
            text="完整扫描",
            height=32,
            corner_radius=10,
            fg_color=COLORS["surface_2"],
            hover_color=COLORS["line"],
            border_width=1,
            border_color=COLORS["line"],
            font=("Microsoft YaHei UI", 10, "bold"),
            command=self._open_sensor_monitor,
        )
        self.sensor_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        self.action_buttons.append(self.sensor_button)

    def _build_output_card(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, padx=24, pady=(20, 0), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self._section_label(top, "03 / 风扇输出").grid(row=0, column=0, sticky="w")
        self.output_status = ctk.CTkLabel(
            top,
            text="未接管",
            text_color=COLORS["amber"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.output_status.grid(row=0, column=1, sticky="e")

        content = ctk.CTkFrame(parent, fg_color="transparent")
        content.grid(row=1, column=0, padx=24, pady=12, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        gauge_box = ctk.CTkFrame(content, fg_color="transparent")
        gauge_box.grid(row=0, column=0, sticky="nsew")
        self.gauge = FanGauge(gauge_box, self.current_speed)
        self.gauge.pack(fill="both", expand=True)

        preset_box = ctk.CTkFrame(content, fg_color="transparent")
        preset_box.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        preset_box.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            preset_box,
            text="快速档位",
            text_color=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 12))

        presets = ((10, "静音"), (15, "日常"), (20, "夏季"), (30, "满载"))
        for index, (percent, label) in enumerate(presets):
            button = ctk.CTkButton(
                preset_box,
                text=f"{percent:02d}%\n{label}",
                height=74,
                corner_radius=14,
                fg_color=COLORS["surface_2"],
                hover_color=COLORS["control_hover"],
                border_width=1,
                border_color=COLORS["line"],
                text_color=COLORS["text"],
                font=("Microsoft YaHei UI", 12, "bold"),
                command=lambda value=percent: self._set_speed(value),
            )
            button.grid(
                row=1 + index // 2,
                column=index % 2,
                padx=(0 if index % 2 == 0 else 6, 6 if index % 2 == 0 else 0),
                pady=(0, 8),
                sticky="ew",
            )
            self.speed_buttons.append(button)

        self.custom_value = tk.IntVar(value=10)
        self.slider_value = ctk.CTkLabel(
            preset_box,
            text="自定义 10%",
            text_color=COLORS["reading"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.slider_value.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 3))
        self.speed_slider = ctk.CTkSlider(
            preset_box,
            from_=5,
            to=100,
            number_of_steps=95,
            variable=self.custom_value,
            command=self._slider_changed,
            progress_color=COLORS["reading"],
            button_color=COLORS["reading"],
            button_hover_color=COLORS["text"],
        )
        self.speed_slider.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 9))
        self.apply_button = ctk.CTkButton(
            preset_box,
            text="应用自定义转速",
            height=38,
            corner_radius=10,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            font=("Microsoft YaHei UI", 11, "bold"),
            command=lambda: self._set_speed(int(self.custom_value.get())),
        )
        self.apply_button.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.speed_buttons.append(self.apply_button)

        warning = ctk.CTkFrame(
            parent,
            corner_radius=9,
            fg_color="#241E12",
            border_width=1,
            border_color="#5C4720",
        )
        warning.grid(row=2, column=0, padx=24, pady=(0, 20), sticky="ew")
        ctk.CTkLabel(
            warning,
            text="温控提示",
            text_color=COLORS["amber"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            warning,
            text="固定低转速会削弱主板保护能力。请持续监控 CPU、内存与硬盘温度。",
            text_color="#E8A33D",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", padx=14, pady=(0, 10))

    def _build_log_panel(self) -> None:
        panel = ctk.CTkFrame(
            self,
            height=128,
            corner_radius=14,
            border_width=1,
            border_color=COLORS["line"],
            fg_color=COLORS["surface"],
        )
        panel.grid(row=2, column=0, padx=22, pady=(0, 20), sticky="ew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_propagate(False)
        self.log_panel = panel
        self._section_label(panel, "04 / 事件日志").grid(
            row=0, column=0, padx=18, pady=(12, 5), sticky="w"
        )
        clear = ctk.CTkButton(
            panel,
            text="清空",
            width=68,
            height=26,
            corner_radius=8,
            fg_color="transparent",
            hover_color=COLORS["surface_2"],
            border_width=1,
            border_color=COLORS["line"],
            font=("Microsoft YaHei UI", 10, "bold"),
            command=lambda: self.log.delete("1.0", "end"),
        )
        clear.grid(row=0, column=1, padx=18, pady=(12, 5), sticky="e")
        self.log = ctk.CTkTextbox(
            panel,
            height=74,
            corner_radius=8,
            fg_color=COLORS["background"],
            border_width=0,
            text_color="#9A9A95",
            font=("Cascadia Mono", 10),
            activate_scrollbars=True,
        )
        self.log.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="ew")

        # Shown instead of the full log when the window is too short for it:
        # the most recent line is nearly always the one that matters.
        self.log_summary = ctk.CTkLabel(
            panel,
            text="",
            anchor="w",
            text_color=COLORS["log_text"],
            font=("Cascadia Mono", 10),
        )
        self.log_summary.grid(
            row=1, column=0, columnspan=2, padx=18, pady=(0, 12), sticky="ew"
        )
        self.log_summary.grid_remove()

    def _settings(self) -> IpmiSettings:
        return IpmiSettings(
            host=self.host_var.get().strip(),
            username=self.user_var.get().strip(),
            password=self.password_var.get(),
            executable=Path(self.exe_var.get().strip()),
        )

    def _open_connection_settings(self) -> None:
        ConnectionDialog(self)

    def _open_sensor_monitor(self) -> None:
        if self.sensor_dialog is not None and self.sensor_dialog._alive():
            self.sensor_dialog.deiconify()
            self.sensor_dialog.lift()
            self.sensor_dialog.focus_force()
            self.sensor_dialog.refresh()
            return
        self.sensor_dialog = SensorDialog(self)

    def _request_sensor_snapshot(self, dialog: SensorDialog) -> bool:
        if not dialog._alive():
            return False
        self._pending_dialog = dialog
        return self.controller.refresh_sensors(
            on_result=dialog.show_result,
            finished=dialog.finish_loading,
        )

    def _refresh_connection_summary(self) -> None:
        settings = self._settings()
        configured = bool(
            settings.host
            and settings.username
            and settings.password
            and str(settings.executable)
        )
        status, tone = presenters.connection_status(configured)
        chip, _tone = presenters.connection_chip(configured)
        color = theme.tone_color(tone)
        self.connection_status_label.configure(text=status, text_color=color)
        self.server_chip.configure(text=chip, text_color=color)

    def _slider_changed(self, value: float) -> None:
        percent = round(value)
        self.slider_value.configure(text=presenters.custom_speed_label(percent))
        self.gauge.set_value(percent)

    def _test_connection(self) -> None:
        """Follow the remembered MAC first, then test - both off the UI thread.

        Reading the ARP table shells out (`arp -a`, up to 10 s on Windows), so
        it cannot happen inline: the window would freeze on every test.
        """
        if not self.remembered_mac:
            self.controller.test_connection(on_success=self._connection_ok)
            return

        mac = self.remembered_mac

        def work() -> None:
            current = discovery.address_for_mac(discovery.read_arp_table(), mac)
            self.after(0, lambda: self._relocated(current))

        threading.Thread(target=work, name="idrac-relocate", daemon=True).start()

    def _relocated(self, current: str | None) -> None:
        if current and current != self.host_var.get().strip():
            self._append_log("SYSTEM", f"iDRAC 地址已变为 {current}，按 MAC 重新定位。")
            self.host_var.set(current)
            self._refresh_connection_summary()
        self.controller.test_connection(on_success=self._connection_ok)

    def _connection_ok(self, _result: CommandResult) -> None:
        chip, tone = presenters.connection_chip(True, online=True)
        self.server_chip.configure(text=chip, text_color=theme.tone_color(tone))

    def _enable_manual(self) -> None:
        self.controller.enable_manual()

    def _restore_auto(self) -> None:
        self.controller.restore_auto()

    def _set_speed(self, percent: int) -> None:
        self.controller.set_speed(percent)

    def _interlock_changed(self, *_args) -> None:
        """Keep the switch and the controller from ever disagreeing.

        The equality guard is what makes the trace safe: on_state() writes the
        var from the controller, which fires this back, which would loop.
        """
        released = bool(self.interlock_var.get())
        if released != self.controller.interlock_released:
            self.controller.set_interlock(released)

    def _update_controls(self) -> None:
        controller = self.controller
        unlocked = controller.interlock_released
        busy = controller.busy
        base_state = "disabled" if busy else "normal"
        for button in self.action_buttons:
            button.configure(state=base_state)

        self.manual_button.configure(state="normal" if unlocked and not busy else "disabled")
        manual = controller.mode == presenters.MODE_MANUAL
        speed_state = "normal" if unlocked and manual and not busy else "disabled"
        for button in self.speed_buttons:
            button.configure(state=speed_state)
        self.speed_slider.configure(state=speed_state)
        text, tone = presenters.output_status(controller.mode, controller.current_speed)
        self.output_status.configure(text=text, text_color=theme.tone_color(tone))

    def _append_log(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp}  [{level:<5}]  {message}"
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log_summary.configure(text=line)


def run(startup_message: str | None = None) -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = FanConsole(startup_message=startup_message)
    app.mainloop()
