"""Startup check: make sure everything the app needs is installed and up to date.

Runs before the main window and uses only the standard library (plain tkinter), because
the app's own packages may be missing, outdated or broken at this point.
"""
import ctypes
import importlib
import importlib.metadata
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
from tkinter import font as tkfont
from tkinter import messagebox

import config

REQUIRED_PACKAGES = ["yt-dlp", "yt-dlp-ejs", "customtkinter", "pillow", "imageio-ffmpeg"]
IMPORT_CHECK = "import yt_dlp, yt_dlp_ejs, customtkinter, PIL, imageio_ffmpeg"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WARNING_AUTO_OPEN_SECONDS = 6

BG = "#121214"
CARD_BG = "#1C1C20"
CARD_BORDER = "#2A2A30"
TEXT = "#F2F2F4"
MUTED = "#9A9AA5"
FAINT = "#55555E"
ACCENT = "#E5322D"
ACCENT_HOVER = "#C42722"
NEUTRAL = "#2A2A31"
NEUTRAL_HOVER = "#383840"
SUCCESS = "#4ADE80"
WARNING = "#FBBF24"
ERROR = "#FF7A75"

PENDING, RUNNING, OK, WARN, FAIL, SKIP = "pending", "running", "ok", "warn", "fail", "skip"
STATUS_STYLE = {
    PENDING: ("•", FAINT),
    RUNNING: ("…", ACCENT),
    OK: ("✓", SUCCESS),
    WARN: ("!", WARNING),
    FAIL: ("✗", ERROR),
    SKIP: ("–", FAINT),
}

STEPS = [
    ("python", "Python"),
    ("internet", "อินเทอร์เน็ต / YouTube"),
    ("packages", "ไลบรารีของโปรแกรม"),
    ("ytdlp", "yt-dlp (ตัวโหลด YouTube)"),
    ("ffmpeg", "ffmpeg (รวมภาพกับเสียง / แปลง MP3)"),
    ("js", "JavaScript runtime (ปลดล็อกความละเอียดสูงของ YouTube)"),
    ("folder", "โฟลเดอร์บันทึกไฟล์"),
]
CRITICAL_STEPS = {"python", "packages"}  # the app cannot open without these
NEEDS_PACKAGES = {"ytdlp", "ffmpeg", "js"}  # these import the app's packages


def venv_python():
    """python.exe beside the running pythonw.exe, so pip's output can be read."""
    exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
    return exe if os.path.exists(exe) else sys.executable


def installed_version(dist):
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def version_tuple(version):
    return tuple(int(n) for n in re.findall(r"\d+", version or ""))


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def program_version(cmd):
    """Version number printed by e.g. `ffmpeg -version`; None if the program doesn't run."""
    try:
        result = run(cmd, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    first_line = result.stdout.strip().splitlines()[0]
    match = re.search(r"\d+(?:\.\d+)+", first_line)
    return match.group(0) if match else first_line[:40]


def last_line(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:150] if lines else "ไม่ทราบสาเหตุ"


def fetch(url, timeout=8):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


class SystemCheck:
    def __init__(self):
        self.proceed = False
        self.busy = False
        self.pip_process = None
        self.ui_queue = queue.Queue()
        self.countdown_job = None
        self.can_install = sys.prefix != sys.base_prefix  # only ever install into the app's .venv

        self.root = tk.Tk()
        self.root.title(f"{config.APP_NAME} — ตรวจสอบระบบ")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(config.ICON_FILE)
        except tk.TclError:
            pass
        self.scale = self.root.winfo_fpixels("1i") / 96
        families = set(tkfont.families(self.root))
        self.family = next((f for f in ("Leelawadee UI", "Segoe UI", "Tahoma") if f in families),
                           "TkDefaultFont")
        self._fonts = {}
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.update_idletasks()
        try:  # dark title bar, matching the main window (Windows 10 20H1+ / 11)
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass
        x = (self.root.winfo_screenwidth() - self.root.winfo_reqwidth()) // 2
        y = (self.root.winfo_screenheight() - self.root.winfo_reqheight()) // 3
        self.root.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def run(self):
        """Show the window, run the checks; True when the app may open."""
        self.root.after(100, self.start)
        self.root.after(50, self._drain_ui_queue)
        self.root.mainloop()
        return self.proceed

    # ---------- window ----------

    def px(self, n):
        return round(n * self.scale)

    def font(self, size, weight="normal"):
        key = (size, weight)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(self.root, family=self.family, size=-self.px(size), weight=weight)
        return self._fonts[key]

    def _build(self):
        outer = tk.Frame(self.root, bg=BG, padx=self.px(28), pady=self.px(24))
        outer.pack(fill="both", expand=True)
        tk.Frame(outer, bg=BG, width=self.px(540), height=0).pack()  # fixes the window width

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        try:
            logo = tk.PhotoImage(file=config.LOGO_FILE)
            self.logo = logo.subsample(max(1, round(logo.width() / self.px(44))))
            tk.Label(header, image=self.logo, bg=BG).pack(side="left", padx=(0, self.px(12)))
        except tk.TclError:
            pass
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text=config.APP_NAME, font=self.font(20, "bold"), fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(titles, text="ตรวจสอบความพร้อมของระบบก่อนเริ่มใช้งาน", font=self.font(12),
                 fg=MUTED, bg=BG).pack(anchor="w")

        card = tk.Frame(outer, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER,
                        padx=self.px(18), pady=self.px(10))
        card.pack(fill="x", pady=(self.px(18), 0))
        self.step_widgets = {}
        for key, name in STEPS:
            row = tk.Frame(card, bg=CARD_BG)
            row.pack(fill="x", pady=self.px(5))
            icon = tk.Label(row, text="", width=2, font=self.font(15, "bold"), bg=CARD_BG, anchor="n")
            icon.pack(side="left", anchor="n")
            texts = tk.Frame(row, bg=CARD_BG)
            texts.pack(side="left", fill="x", expand=True, padx=(self.px(6), 0))
            tk.Label(texts, text=name, font=self.font(13, "bold"), fg=TEXT, bg=CARD_BG, anchor="w").pack(anchor="w")
            detail = tk.Label(texts, text="", font=self.font(12), fg=MUTED, bg=CARD_BG, anchor="w",
                              justify="left", wraplength=self.px(440))
            detail.pack(anchor="w", fill="x")
            self.step_widgets[key] = (icon, detail)

        self.activity_label = tk.Label(outer, text="", font=self.font(11), fg=FAINT, bg=BG, anchor="w")
        self.activity_label.pack(fill="x", pady=(self.px(10), 0))
        self.track = tk.Frame(outer, bg=NEUTRAL, height=self.px(4))
        self.track.pack(fill="x", pady=(self.px(4), 0))
        self.slider = tk.Frame(self.track, bg=ACCENT)
        self.slider_pos = 0.0

        self.result_label = tk.Label(outer, text="", font=self.font(15, "bold"), bg=BG, anchor="w",
                                     justify="left", wraplength=self.px(540))
        self.result_label.pack(fill="x", pady=(self.px(14), 0))
        self.button_bar = tk.Frame(outer, bg=BG)
        self.button_bar.pack(fill="x", pady=(self.px(12), 0))

    def _button(self, text, command, primary=False):
        bg, hover = (ACCENT, ACCENT_HOVER) if primary else (NEUTRAL, NEUTRAL_HOVER)
        button = tk.Button(self.button_bar, text=text, command=command, bg=bg, fg=TEXT,
                           activebackground=hover, activeforeground=TEXT, relief="flat", bd=0,
                           highlightthickness=0, cursor="hand2", padx=self.px(18), pady=self.px(7),
                           font=self.font(13, "bold" if primary else "normal"))
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=bg))
        button.pack(side="right", padx=(self.px(8), 0))
        return button

    def _animate(self):
        if not self.busy:
            self.slider.place_forget()
            return
        self.slider_pos = (self.slider_pos + 0.015) % 1.3
        self.slider.place(relx=self.slider_pos - 0.3, rely=0, relwidth=0.3, relheight=1)
        self.root.after(30, self._animate)

    def _show_step(self, key, status, detail):
        icon, detail_label = self.step_widgets[key]
        symbol, color = STATUS_STYLE[status]
        icon.configure(text=symbol, fg=color)
        detail_label.configure(text=detail, fg=ERROR if status == FAIL else WARNING if status == WARN else MUTED)

    def _show_activity(self, text):
        font, width = self.font(11), self.px(540)
        if font.measure(text) > width:
            while text and font.measure(text + "…") > width:
                text = text[:-1]
            text += "…"
        self.activity_label.configure(text=text)

    # ---------- running the checks ----------

    def post(self, fn, *args):
        """The checks run on a worker thread; only the UI thread may touch Tk."""
        self.ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        while True:
            try:
                fn, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            fn(*args)
        self.root.after(50, self._drain_ui_queue)

    def set_step(self, key, status, detail):
        self.post(self._show_step, key, status, detail)

    def activity(self, text):
        self.post(self._show_activity, text)

    def start(self):
        if self.countdown_job:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None
        for widget in self.button_bar.winfo_children():
            widget.destroy()
        self.result_label.configure(text="กำลังตรวจสอบ…", fg=MUTED)
        for key, _ in STEPS:
            self._show_step(key, PENDING, "รอตรวจสอบ")
        self.busy = True
        self._animate()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        self.online = True
        self.packages_ok = False
        self.changes = []
        results = {}
        for key, _ in STEPS:
            if key in NEEDS_PACKAGES and not self.packages_ok:
                results[key] = SKIP
                self.set_step(key, SKIP, "ข้าม — ไลบรารียังไม่พร้อม")
                continue
            self.set_step(key, RUNNING, "กำลังตรวจสอบ…")
            try:
                status, detail = getattr(self, "check_" + key)()
            except Exception as exc:
                config.log.exception("system check step %r crashed", key)
                status = FAIL if key in CRITICAL_STEPS else WARN
                detail = f"ตรวจสอบไม่สำเร็จ: {exc}"
            if status != OK:
                config.log.warning("system check %s: %s - %s", key, status, detail)
            results[key] = status
            self.set_step(key, status, detail)
        self.activity("")
        self.post(self._finish, results)

    def _finish(self, results):
        self.busy = False
        if FAIL in results.values():
            self.result_label.configure(text="ระบบยังไม่พร้อมใช้งาน — แก้ไขรายการที่มีเครื่องหมาย ✗ แล้วกดลองใหม่",
                                        fg=ERROR)
            self._button("ลองใหม่", self.start, primary=True)
            self._button("เปิด error.log", self._open_log)
            self._button("ปิด", self._on_close)
            self.root.bell()
        elif WARN in results.values():
            self.result_label.configure(text="ใช้งานได้ แต่มีคำเตือน (รายการที่มีเครื่องหมาย !)", fg=WARNING)
            self.open_button = self._button("", self._open_app, primary=True)
            self._button("ตรวจสอบใหม่", self.start)
            self._countdown(WARNING_AUTO_OPEN_SECONDS)
        else:
            done = "ติดตั้ง/อัปเดตเรียบร้อย — " if self.changes else ""
            self.result_label.configure(text=f"{done}ระบบพร้อมใช้งาน ✓", fg=SUCCESS)
            self.root.after(1500 if self.changes else 500, self._open_app)

    def _countdown(self, seconds):
        if seconds <= 0:
            self._open_app()
            return
        self.open_button.configure(text=f"เปิดโปรแกรม ({seconds})")
        self.countdown_job = self.root.after(1000, lambda: self._countdown(seconds - 1))

    def _open_app(self):
        self.proceed = True
        self.root.destroy()

    def _open_log(self):
        if not os.path.exists(config.LOG_FILE):
            open(config.LOG_FILE, "a").close()
        os.startfile(config.LOG_FILE)

    def _on_close(self):
        if self.busy and self.pip_process and self.pip_process.poll() is None:
            if not messagebox.askyesno(config.APP_NAME, "กำลังติดตั้งอยู่ ต้องการยกเลิกและปิดหรือไม่?",
                                       parent=self.root):
                return
            self.pip_process.terminate()
        self.root.destroy()

    def pip_install(self, args):
        """pip install into the app's .venv, showing pip's progress on the activity line."""
        cmd = [venv_python(), "-m", "pip", "install", "--disable-pip-version-check",
               "--progress-bar", "off", *args]
        config.log.info("running: %s", " ".join(cmd))
        output = []
        try:
            self.pip_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            for line in self.pip_process.stdout:
                if line.strip():
                    output.append(line.rstrip())
                    self.activity(line.strip())
            code = self.pip_process.wait()
        except OSError as exc:
            output.append(str(exc))
            code = -1
        importlib.invalidate_caches()
        if code != 0:
            config.log.error("pip failed (exit %s):\n%s", code, "\n".join(output[-40:]))
        return code == 0

    # ---------- the checks: each returns (status, detail) ----------

    def check_python(self):
        v = sys.version_info
        version = f"{v.major}.{v.minor}.{v.micro}"
        if v < (3, 10):
            return FAIL, f"{version} — ต้องใช้ Python 3.10 ขึ้นไป"
        if not self.can_install:
            return WARN, f"{version} — ไม่ได้เปิดผ่าน convert_link_youtube_to_mp4.bat จึงจะไม่ติดตั้งอะไรเพิ่มให้"
        return OK, version

    def check_internet(self):
        try:
            fetch("https://www.youtube.com/generate_204")
        except Exception as exc:
            self.online = False
            config.log.warning("internet check failed: %s", exc)
            return WARN, "เชื่อมต่อ YouTube ไม่ได้ — ตรวจสอบอินเทอร์เน็ต (ตอนนี้จะยังดาวน์โหลดไม่ได้)"
        return OK, "เชื่อมต่อได้"

    def check_packages(self):
        python = venv_python()
        if self.can_install and run([python, "-m", "pip", "--version"]).returncode != 0:
            self.set_step("packages", RUNNING, "กำลังซ่อม pip…")
            run([python, "-m", "ensurepip", "--upgrade"], timeout=300)

        missing = [p for p in REQUIRED_PACKAGES if not installed_version(p)]
        ctk_version = installed_version("customtkinter")
        if ctk_version and version_tuple(ctk_version)[:1] != (6,):
            missing.append(f"customtkinter (มี {ctk_version} ต้องการ 6.x)")
        installed_now = None
        if missing:
            names = ", ".join(missing)
            if not self.can_install:
                return FAIL, f"ขาด: {names}"
            if not self.online:
                return FAIL, f"ขาด: {names} — ต้องต่ออินเทอร์เน็ตเพื่อติดตั้ง"
            self.set_step("packages", RUNNING, f"กำลังติดตั้ง: {names}")
            if not self.pip_install(["-r", config.REQUIREMENTS_FILE]):
                return FAIL, "ติดตั้งไม่สำเร็จ — ดูรายละเอียดใน error.log"
            installed_now = f"ติดตั้งเพิ่มแล้ว: {names}"

        # Installed isn't the same as working (e.g. an interrupted install), so try importing them.
        result = run([python, "-c", IMPORT_CHECK], timeout=120)
        if result.returncode != 0:
            reason = last_line(result.stderr)
            config.log.error("import check failed:\n%s", result.stderr)
            if not (self.can_install and self.online):
                return FAIL, f"ไลบรารีเสีย: {reason}"
            self.set_step("packages", RUNNING, "ไลบรารีเสีย กำลังติดตั้งใหม่…")
            if not self.pip_install(["--force-reinstall", "-r", config.REQUIREMENTS_FILE]) \
                    or run([python, "-c", IMPORT_CHECK], timeout=120).returncode != 0:
                return FAIL, f"ไลบรารีเสีย ซ่อมไม่สำเร็จ: {reason}"
            installed_now = "ติดตั้งไลบรารีใหม่ทั้งหมดแล้ว"

        self.packages_ok = True
        if installed_now:
            self.changes.append(installed_now)
            return OK, installed_now
        return OK, f"ครบ {len(REQUIRED_PACKAGES)} ตัว"

    def check_ytdlp(self):
        # YouTube changes often and an old yt-dlp stops working, so keep it on the latest release.
        current = installed_version("yt-dlp")
        try:
            latest = json.loads(fetch("https://pypi.org/pypi/yt-dlp/json"))["info"]["version"]
        except Exception as exc:
            config.log.warning("could not read latest yt-dlp version: %s", exc)
            return WARN, f"{current} — ตรวจสอบเวอร์ชันล่าสุดไม่ได้"
        if version_tuple(current) >= version_tuple(latest):
            return OK, f"{current} (ล่าสุด)"
        if not self.can_install:
            return WARN, f"{current} — มีเวอร์ชันใหม่ {latest}"
        self.set_step("ytdlp", RUNNING, f"กำลังอัปเดต {current} → {latest}…")
        if not self.pip_install(["--upgrade", "yt-dlp[default]"]):
            return WARN, f"อัปเดตเป็น {latest} ไม่สำเร็จ (ใช้ {current} ต่อไปก่อน)"
        updated = f"อัปเดตจาก {current} เป็น {installed_version('yt-dlp')} แล้ว"
        self.changes.append(updated)
        return OK, updated

    def check_ffmpeg(self):
        import downloader  # safe now: packages are installed and yt-dlp is up to date

        exe = downloader.find_ffmpeg()
        version = exe and program_version([exe, "-version"])
        if not version and self.can_install and self.online:
            self.set_step("ffmpeg", RUNNING, "ffmpeg ใช้งานไม่ได้ กำลังติดตั้งใหม่…")
            self.pip_install(["--force-reinstall", "--no-deps", "imageio-ffmpeg"])
            downloader.find_ffmpeg.cache_clear()
            exe = downloader.find_ffmpeg()
            version = exe and program_version([exe, "-version"])
            if version:
                self.changes.append("ติดตั้ง ffmpeg ใหม่")
        if not version:
            return WARN, "ไม่พบ ffmpeg — โหลด MP4 1080p / MP3 ไม่ได้"
        return OK, f"ffmpeg {version}"

    def check_js(self):
        import downloader

        def working_runtime():
            for name, cfg in downloader.find_js_runtimes().items():
                version = program_version([cfg["path"], "--version"])
                if version:
                    return f"{name} {version}"
            return None

        found = working_runtime()
        if found:
            return OK, found
        if not (self.can_install and self.online):
            return WARN, "ไม่พบ Node.js / Deno — บางคลิปอาจโหลดได้แค่ความละเอียดต่ำ"
        self.set_step("js", RUNNING, "ไม่พบ Node.js / Deno กำลังติดตั้ง Deno (ประมาณ 40 MB)…")
        if self.pip_install(["deno"]):
            downloader.find_js_runtimes.cache_clear()
            found = working_runtime()
        if not found:
            return WARN, "ติดตั้ง Deno ไม่สำเร็จ — บางคลิปอาจโหลดได้แค่ความละเอียดต่ำ"
        self.changes.append(f"ติดตั้ง {found}")
        return OK, f"ติดตั้ง {found} แล้ว"

    def check_folder(self):
        path = config.download_dir(config.load_settings())
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as exc:
            return WARN, f"{path}\nเขียนไฟล์ไม่ได้ ({exc.strerror}) — เปลี่ยนโฟลเดอร์ได้ในหน้าโปรแกรม"
        return OK, path


def run_check():
    return SystemCheck().run()
