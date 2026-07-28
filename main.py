import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox

from r730xd_fan.dependency import (
    ensure_ipmitool_available,
    install_bmc_elevated,
)
from r730xd_fan.ui import run


def show_startup_error(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror("R730xd Fan Console", message, parent=root)
        root.destroy()
    except Exception:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "R730xd Fan Console", 0x10)


def write_startup_error() -> None:
    folder = Path.home() / "AppData" / "Local" / "R730xdFanConsole"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    if "--install-bmc" in sys.argv:
        try:
            raise SystemExit(install_bmc_elevated())
        except Exception:
            write_startup_error()
            raise SystemExit(1) from None
    try:
        dependency = ensure_ipmitool_available()
    except Exception as exc:
        write_startup_error()
        show_startup_error(str(exc))
        raise SystemExit(1) from exc
    run(startup_message=dependency.message)
