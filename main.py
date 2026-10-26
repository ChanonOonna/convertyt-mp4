"""Entry point: check the system (install/update whatever is missing), then open the app."""
import ctypes
import sys

import config


def show_fatal_error(exc):
    """Last resort: the app runs under pythonw, so without this a crash would be silent."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(config.APP_NAME,
                             f"โปรแกรมหยุดทำงานเพราะเกิดข้อผิดพลาด\n\n{type(exc).__name__}: {exc}\n\n"
                             f"รายละเอียดถูกบันทึกไว้ที่\n{config.LOG_FILE}")
        root.destroy()
    except Exception:
        pass


def main():
    config.setup_logging()
    if sys.platform == "win32":
        try:  # sharp text on high-DPI screens, for the check window and the app alike
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        try:  # own taskbar entry, so it shows our icon instead of Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("botdowyoutomp4.YouTubeDownloader")
        except Exception:
            pass
    try:
        import system_check
        if not system_check.run_check():
            return
        import app
        app.main()
    except Exception as exc:
        config.log.exception("fatal error")
        show_fatal_error(exc)


if __name__ == "__main__":
    main()
