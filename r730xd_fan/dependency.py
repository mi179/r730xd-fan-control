from __future__ import annotations

import ctypes
import hashlib
import os
import queue
import subprocess
import sys
import threading
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .config import discover_ipmitool


BMC_MSI_NAME = "BMC.msi"
BMC_MSI_SHA256 = "13F2179F622A0AB536B2FA26772AC2E05B5F95993C15A45DD99429F20EC09E15"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
INSTALL_TIMEOUT_MS = 15 * 60 * 1000
MUTEX_NAME = r"Global\R730xdFanConsole-BmcInstall"


@dataclass(frozen=True, slots=True)
class DependencyResult:
    executable: Path
    installed_now: bool
    restart_required: bool = False

    @property
    def message(self) -> str:
        if self.installed_now:
            suffix = "；Windows 需要重启" if self.restart_required else ""
            return f"Dell BMC Utilities 已自动安装{suffix}。ipmitool: {self.executable}"
        return f"已检测到 Dell ipmitool：{self.executable}"


class DependencyInstallError(RuntimeError):
    pass


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


if os.name == "nt":
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _userenv = ctypes.WinDLL("userenv", use_last_error=True)

    _shell_execute_ex = _shell32.ShellExecuteExW
    _shell_execute_ex.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    _shell_execute_ex.restype = wintypes.BOOL

    _wait_for_single_object = _kernel32.WaitForSingleObject
    _wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _wait_for_single_object.restype = wintypes.DWORD

    _get_exit_code_process = _kernel32.GetExitCodeProcess
    _get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _get_exit_code_process.restype = wintypes.BOOL

    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL

    _get_system_directory = _kernel32.GetSystemDirectoryW
    _get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    _get_system_directory.restype = wintypes.UINT

    _create_mutex = _kernel32.CreateMutexW
    _create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _create_mutex.restype = wintypes.HANDLE

    _release_mutex_handle = _kernel32.ReleaseMutex
    _release_mutex_handle.argtypes = [wintypes.HANDLE]
    _release_mutex_handle.restype = wintypes.BOOL

    _get_all_users_profile_directory = _userenv.GetAllUsersProfileDirectoryW
    _get_all_users_profile_directory.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _get_all_users_profile_directory.restype = wintypes.BOOL

    _is_user_an_admin = _shell32.IsUserAnAdmin
    _is_user_an_admin.argtypes = []
    _is_user_an_admin.restype = wintypes.BOOL


def bundled_bmc_msi() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "payload" / BMC_MSI_NAME
    return Path(os.getenv("BMC_MSI_PATH", r"C:\OpenManage\BMC.msi"))


def verify_bmc_payload(path: Path) -> None:
    if not path.is_file():
        raise DependencyInstallError(f"一体包中缺少 Dell {BMC_MSI_NAME}：{path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    if digest != BMC_MSI_SHA256:
        raise DependencyInstallError("Dell BMC 安装载荷校验失败，已停止自动安装。")


def ensure_ipmitool_available() -> DependencyResult:
    existing = discover_ipmitool()
    if existing.is_file():
        return DependencyResult(existing, installed_now=False)

    if os.name != "nt":
        raise DependencyInstallError("当前系统未找到 ipmitool；自动安装仅支持 Windows。")

    verify_bmc_payload(bundled_bmc_msi())
    exit_code = _install_with_status()
    if exit_code not in {0, 1641, 3010}:
        raise DependencyInstallError(f"Dell BMC 自动安装失败，返回代码 {exit_code}。")

    installed = discover_ipmitool()
    if not installed.is_file():
        if exit_code in {1641, 3010}:
            raise DependencyInstallError("Dell BMC 已安装，但需要重启 Windows 后才能使用。")
        raise DependencyInstallError("安装已结束，但仍未找到 ipmitool.exe。")
    return DependencyResult(installed, installed_now=True, restart_required=exit_code in {1641, 3010})


def install_bmc_elevated() -> int:
    """Install the embedded MSI from an elevated copy of this same executable."""
    if os.name != "nt" or not _is_user_an_admin():
        raise DependencyInstallError("Dell BMC 安装子进程没有管理员权限。")

    mutex = _acquire_install_mutex()
    try:
        existing = discover_ipmitool()
        if existing.is_file():
            return 0

        source = bundled_bmc_msi()
        verify_bmc_payload(source)
        stage = _create_secure_stage()
        staged_msi = stage / BMC_MSI_NAME
        log_path = stage / "install-bmc.log"
        staged_msi.write_bytes(source.read_bytes())
        verify_bmc_payload(staged_msi)

        msiexec = system_executable("msiexec.exe")
        completed = subprocess.run(
            [
                str(msiexec),
                "/i",
                str(staged_msi),
                "/qn",
                "/norestart",
                "/L*v",
                str(log_path),
            ],
            cwd=str(msiexec.parent),
            timeout=15 * 60,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        staged_msi.unlink(missing_ok=True)
        return int(completed.returncode)
    finally:
        _close_install_mutex(mutex)


def system_executable(name: str) -> Path:
    if os.name != "nt":
        raise DependencyInstallError("Windows 系统程序只能在 Windows 中解析。")
    buffer = ctypes.create_unicode_buffer(32768)
    length = _get_system_directory(buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise DependencyInstallError(f"无法解析 Windows System32 目录，错误 {ctypes.get_last_error()}。")
    executable = Path(buffer.value) / name
    if not executable.is_file():
        raise DependencyInstallError(f"找不到 Windows 系统程序：{executable}")
    return executable


def program_data_directory() -> Path:
    """Resolve the machine-wide data directory without trusting inherited environment variables."""
    if os.name != "nt":
        raise DependencyInstallError("ProgramData 目录只能在 Windows 中解析。")
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    if not _get_all_users_profile_directory(buffer, ctypes.byref(size)):
        raise DependencyInstallError(f"无法解析 Windows ProgramData 目录，错误 {ctypes.get_last_error()}。")
    directory = Path(buffer.value)
    if not directory.is_absolute() or not directory.is_dir():
        raise DependencyInstallError(f"Windows 返回了无效的 ProgramData 目录：{directory}")
    return directory


def _install_with_status() -> int:
    import tkinter as tk
    from tkinter import ttk

    result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
    root = tk.Tk()
    root.title("R730xd Fan Console · 首次运行准备")
    root.geometry("560x260")
    root.resizable(False, False)
    root.configure(bg="#07111F")
    root.attributes("-topmost", True)
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    tk.Label(
        root,
        text="正在准备 Dell 管理组件",
        bg="#07111F",
        fg="#F2F7FC",
        font=("Microsoft YaHei UI", 18, "bold"),
    ).pack(anchor="w", padx=28, pady=(30, 6))
    tk.Label(
        root,
        text="未检测到 ipmitool，请允许一次管理员权限请求；完成后自动进入调速界面。",
        bg="#07111F",
        fg="#8FA6BC",
        font=("Microsoft YaHei UI", 10),
        wraplength=500,
        justify="left",
    ).pack(anchor="w", padx=28, pady=(0, 20))

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "Bmc.Horizontal.TProgressbar",
        troughcolor="#11243A",
        background="#3B82F6",
        bordercolor="#11243A",
        lightcolor="#3B82F6",
        darkcolor="#3B82F6",
    )
    progress = ttk.Progressbar(root, mode="indeterminate", style="Bmc.Horizontal.TProgressbar")
    progress.pack(fill="x", padx=28, pady=(0, 18))
    progress.start(12)
    tk.Label(
        root,
        text="首次缺少工具时执行一次 · 最长等待 15 分钟",
        bg="#07111F",
        fg="#38BDF8",
        font=("Cascadia Mono", 9),
    ).pack(anchor="w", padx=28)

    def worker() -> None:
        try:
            result_queue.put(("ok", _run_elevated_self()))
        except Exception as exc:
            result_queue.put(("error", exc))

    def poll() -> None:
        try:
            kind, value = result_queue.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        progress.stop()
        root.destroy()
        if kind == "ok":
            result.append(int(value))
        else:
            error.append(value)

    result: list[int] = []
    error: list[object] = []
    threading.Thread(target=worker, name="bmc-auto-installer", daemon=True).start()
    root.after(100, poll)
    root.mainloop()

    if error:
        raise DependencyInstallError(str(error[0]))
    if not result:
        raise DependencyInstallError("Dell BMC 自动安装意外中止。")
    return result[0]


def _run_elevated_self() -> int:
    if getattr(sys, "frozen", False):
        target = Path(sys.executable).resolve()
        parameters = "--install-bmc"
    else:
        target = Path(sys.executable).resolve()
        script = Path(sys.argv[0]).resolve()
        parameters = f'"{script}" --install-bmc'

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = str(target)
    info.lpParameters = parameters
    info.lpDirectory = str(target.parent)
    info.nShow = SW_HIDE

    ctypes.set_last_error(0)
    if not _shell_execute_ex(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == 1223:
            raise DependencyInstallError("你取消了管理员权限请求，Dell 工具未安装。")
        raise DependencyInstallError(f"无法启动 Dell 安装子进程，Windows 错误 {code}。")
    if not info.hProcess:
        raise DependencyInstallError("Windows 没有返回安装子进程句柄。")

    try:
        wait_result = _wait_for_single_object(info.hProcess, INSTALL_TIMEOUT_MS)
        if wait_result == WAIT_TIMEOUT:
            raise DependencyInstallError("Dell 安装超过 15 分钟，安装进程可能仍在后台运行。")
        if wait_result == WAIT_FAILED:
            raise DependencyInstallError(f"等待安装进程失败，Windows 错误 {ctypes.get_last_error()}。")
        if wait_result != WAIT_OBJECT_0:
            raise DependencyInstallError(f"等待安装进程返回异常状态：{wait_result}。")

        exit_code = wintypes.DWORD()
        if not _get_exit_code_process(info.hProcess, ctypes.byref(exit_code)):
            raise DependencyInstallError(
                f"无法读取 Dell 安装进程退出状态，Windows 错误 {ctypes.get_last_error()}。"
            )
        return int(exit_code.value)
    finally:
        _close_handle(info.hProcess)


def _create_secure_stage() -> Path:
    program_data = program_data_directory()
    stage = program_data / f"R730xdFanConsole-{uuid.uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    if _is_reparse_point(stage):
        raise DependencyInstallError("安全暂存目录是重解析点，已停止安装。")

    icacls = system_executable("icacls.exe")
    acl = subprocess.run(
        [
            str(icacls),
            str(stage),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if acl.returncode != 0:
        raise DependencyInstallError(f"无法保护安装暂存目录：{acl.stderr.strip()}")
    owner = subprocess.run(
        [str(icacls), str(stage), "/setowner", "*S-1-5-32-544"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if owner.returncode != 0:
        raise DependencyInstallError(f"无法设置安装暂存目录所有者：{owner.stderr.strip()}")
    return stage


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _acquire_install_mutex() -> wintypes.HANDLE:
    ctypes.set_last_error(0)
    handle = _create_mutex(None, True, MUTEX_NAME)
    if not handle:
        raise DependencyInstallError(f"无法创建安装互斥锁，Windows 错误 {ctypes.get_last_error()}。")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        wait_result = _wait_for_single_object(handle, INSTALL_TIMEOUT_MS)
        if wait_result != WAIT_OBJECT_0:
            _close_handle(handle)
            raise DependencyInstallError("另一个 Dell 安装实例长时间未完成。")
    return handle


def _close_install_mutex(handle: wintypes.HANDLE) -> None:
    if handle:
        _release_mutex_handle(handle)
        _close_handle(handle)
