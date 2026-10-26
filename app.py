"""Desktop app: paste YouTube links, pick MP4 1080p/720p/480p or MP3 per item, download all."""
import collections
import io
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

import config
import downloader

MAX_ITEMS = downloader.MAX_ITEMS
MAX_PARALLEL_DOWNLOADS = 2
ROWS_PER_TICK = 20  # rows built per UI tick, so a long playlist doesn't freeze the window
THUMB_SIZE = (112, 63)

WINDOW_BG = "#121214"
CARD_BG = "#1C1C20"
CARD_BORDER = "#2A2A30"
THUMB_BG = "#26262C"
TEXT = "#F2F2F4"
MUTED = "#9A9AA5"
FAINT = "#55555E"
ACCENT = "#E5322D"
ACCENT_HOVER = "#C42722"
ACCENT_DIM = "#6B2421"
NEUTRAL = "#2A2A31"
NEUTRAL_HOVER = "#383840"
SUCCESS = "#4ADE80"
WARNING = "#FBBF24"
ERROR = "#FF7A75"
BANNER_STYLES = {  # kind -> (background, accent, icon)
    "error": ("#3A1618", ERROR, "✗"),
    "warning": ("#3A2E12", WARNING, "!"),
    "success": ("#12301F", SUCCESS, "✓"),
}

ACTIVE_STATES = ("queued", "downloading")
PENDING_STATES = ("ready", "error", "cancelled")


def human_speed(bytes_per_s):
    if not bytes_per_s:
        return ""
    if bytes_per_s >= 1024 * 1024:
        return f"{bytes_per_s / 1024 / 1024:.1f} MB/s"
    return f"{bytes_per_s / 1024:.0f} KB/s"


def fit_text(text, font, max_width):
    """Cut text with an ellipsis so it fits in max_width pixels of `font`."""
    if font.measure(text) <= max_width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def folder_problem(path):
    """None if files can be saved in `path`, else a short reason."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as exc:
        return exc.strerror or str(exc)
    return None


# The list can hold 50 videos, so its rows use plain Tk widgets (~1 ms each) instead of
# CustomTkinter ones (~140 ms per row), styled to match the rest of the window.

class FlatButton(tk.Label):
    def __init__(self, master, app, text, command, bg=NEUTRAL, hover=NEUTRAL_HOVER, fg=TEXT,
                 size=12, padx=12, pady=5):
        super().__init__(master, text=text, bg=bg, fg=fg, font=app.tk_font(size), bd=0,
                         padx=app.px(padx), pady=app.px(pady), cursor="hand2")
        self._bg, self._hover, self._fg = bg, hover, fg
        self.command = command
        self.enabled = True
        self.bind("<Enter>", lambda _e: self.enabled and self.configure(bg=self._hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._bg))
        self.bind("<ButtonRelease-1>", lambda _e: self.enabled and self.command())

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(bg=self._bg, fg=self._fg if enabled else FAINT,
                       cursor="hand2" if enabled else "arrow")


class FormatPicker(tk.Frame):
    """MP4 1080p | MP4 720p | MP4 480p | MP3 as a row of pills."""

    def __init__(self, master, app, value, command=None):
        super().__init__(master, bg=NEUTRAL)
        self.value = value
        self.command = command
        self.enabled = True
        self.pills = {}
        for fmt in downloader.FORMATS:
            pill = tk.Label(self, text=fmt, font=app.tk_font(12, "bold"), bd=0,
                            padx=app.px(10), pady=app.px(4))
            pill.pack(side="left", padx=app.px(2), pady=app.px(2))
            pill.bind("<ButtonRelease-1>", lambda _e, f=fmt: self._click(f))
            pill.bind("<Enter>", lambda _e, f=fmt: self._hover(f, True))
            pill.bind("<Leave>", lambda _e, f=fmt: self._hover(f, False))
            self.pills[fmt] = pill
        self._paint()

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self._paint()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._paint()

    def _click(self, fmt):
        if self.enabled and fmt != self.value:
            self.set(fmt)
            if self.command:
                self.command(fmt)

    def _hover(self, fmt, inside):
        if self.enabled and fmt != self.value:
            self.pills[fmt].configure(bg=NEUTRAL_HOVER if inside else NEUTRAL)

    def _paint(self):
        for fmt, pill in self.pills.items():
            selected = fmt == self.value
            if selected:
                bg = ACCENT if self.enabled else ACCENT_DIM
            else:
                bg = NEUTRAL
            pill.configure(bg=bg, fg=TEXT if self.enabled or selected else FAINT,
                           cursor="hand2" if self.enabled and not selected else "arrow")


class ItemRow(tk.Frame):
    """One video in the list: thumbnail, title, format picker, progress."""

    def __init__(self, master, app, item, fmt):
        super().__init__(master, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER,
                         highlightcolor=CARD_BORDER)
        self.app = app
        self.item = item
        self.state = "ready"
        self.filepath = None
        self.error = None
        self.done_format = None
        self.thumb_image = None
        px = app.px
        self.grid_columnconfigure(1, weight=1)

        thumb_box = tk.Frame(self, bg=THUMB_BG, width=px(THUMB_SIZE[0]), height=px(THUMB_SIZE[1]))
        thumb_box.grid(row=0, column=0, rowspan=3, padx=px(12), pady=px(12))
        thumb_box.grid_propagate(False)
        self.thumb = tk.Label(thumb_box, bg=THUMB_BG, fg=FAINT, text="▶", font=app.tk_font(18), bd=0)
        self.thumb.place(relwidth=1, relheight=1)
        self.thumb_box = thumb_box

        self.title_label = tk.Label(self, text=item["title"], font=app.title_font, fg=TEXT, bg=CARD_BG,
                                    anchor="w", bd=0)
        self.title_label.grid(row=0, column=1, sticky="ew", pady=(px(12), 0))

        meta = " · ".join(p for p in (item["channel"], downloader.format_duration(item["duration"])) if p)
        tk.Label(self, text=meta, font=app.tk_font(12), fg=MUTED, bg=CARD_BG, anchor="w", bd=0).grid(
            row=1, column=1, sticky="ew", pady=(px(4), 0))

        self.status_label = tk.Label(self, text="", font=app.tk_font(12), fg=MUTED, bg=CARD_BG,
                                     anchor="w", bd=0)
        self.status_label.grid(row=2, column=1, sticky="ew", pady=(px(4), px(12)))

        self.format_picker = FormatPicker(self, app, fmt, self._on_format_picked)
        self.format_picker.grid(row=0, column=2, rowspan=2, padx=(px(12), px(6)), pady=(px(12), 0), sticky="e")

        self.remove_button = FlatButton(self, app, "✕", lambda: app.remove_row(self), bg=CARD_BG,
                                        fg=MUTED, size=14, padx=8, pady=2)
        self.remove_button.grid(row=0, column=3, rowspan=2, padx=(0, px(12)), pady=(px(12), 0))

        self.progress_track = tk.Frame(self, bg=NEUTRAL, height=px(6))
        self.progress_fill = tk.Frame(self.progress_track, bg=ACCENT)
        self.progress_fill.place(x=0, y=0, relheight=1, relwidth=0)
        self.open_button = FlatButton(self, app, "เปิดโฟลเดอร์", lambda: app.reveal_file(self.filepath))

        self.set_state("ready")
        self.bind("<Configure>", self._fit_title)

    def _fit_title(self, _event=None):
        used = (self.thumb_box.winfo_width() + self.format_picker.winfo_width()
                + self.remove_button.winfo_width() + self.app.px(60))
        available = self.winfo_width() - used
        if available > 40:
            text = fit_text(self.item["title"], self.app.title_font, available)
            if text != self.title_label.cget("text"):
                self.title_label.configure(text=text)

    def _on_format_picked(self, value):
        # Picking a different format for a finished item queues it again in that format.
        if self.state == "done" and value != self.done_format:
            self.set_state("ready")
            self.app.refresh_counts()

    def set_format(self, value):
        if self.state not in ACTIVE_STATES:
            self.format_picker.set(value)
            self._on_format_picked(value)

    def set_thumbnail(self, image):
        if self.winfo_exists():
            self.thumb_image = ImageTk.PhotoImage(image)
            self.thumb.configure(image=self.thumb_image, text="")

    def set_state(self, state, detail=None):
        self.state = state
        active = state in ACTIVE_STATES
        self.format_picker.set_enabled(not active)
        self.remove_button.set_enabled(not active)
        self.progress_track.grid_remove()
        self.open_button.grid_remove()
        px = self.app.px

        if state == "ready":
            self._status("พร้อมดาวน์โหลด", MUTED)
        elif state == "queued":
            self._status("รอคิว…", MUTED)
        elif state == "downloading":
            self._status("กำลังเริ่ม…", TEXT)
            self.progress_fill.place_configure(relwidth=0)
            self.progress_track.grid(row=2, column=2, columnspan=2, sticky="ew", padx=px(12), pady=(0, px(8)))
        elif state == "done":
            self.filepath = detail
            self.done_format = self.format_picker.get()
            self._status(f"✓ เสร็จแล้ว · {os.path.basename(detail)}", SUCCESS)
            self.open_button.grid(row=2, column=2, columnspan=2, sticky="e", padx=px(12), pady=(0, px(10)))
        elif state == "error":
            self.error = detail
            self._status(f"✗ {detail}", ERROR)
        elif state == "cancelled":
            self._status("หยุดแล้ว", MUTED)

    def show_progress(self, fraction, speed, eta, phase):
        if self.state != "downloading":
            return
        if phase == "convert":
            self.progress_fill.place_configure(relwidth=1)
            self._status("กำลังรวม/แปลงไฟล์…", TEXT)
            return
        parts = []
        if fraction is not None:
            self.progress_fill.place_configure(relwidth=fraction)
            parts.append(f"{fraction * 100:.0f}%")
        if speed:
            parts.append(human_speed(speed))
        if eta:
            parts.append(f"เหลือ {downloader.format_duration(eta)}")
        self._status("กำลังดาวน์โหลด  " + " · ".join(parts), TEXT)

    def _status(self, text, color):
        self.status_label.configure(text=text, fg=color)


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=WINDOW_BG)
        self.title(config.APP_NAME)
        self.geometry("1040x720")
        self.minsize(900, 560)
        if os.path.exists(config.ICON_FILE):
            try:
                self.iconbitmap(config.ICON_FILE)
            except tk.TclError:
                pass

        self.settings = config.load_settings()
        self.download_dir = config.download_dir(self.settings)
        fmt = self.settings.get("default_format")
        self.default_format = fmt if fmt in downloader.FORMATS else downloader.DEFAULT_FORMAT

        families = set(tkfont.families(self))
        self.font_family = next((f for f in ("Leelawadee UI", "Segoe UI", "Tahoma") if f in families),
                                "TkDefaultFont")
        self.scale = ctk.ScalingTracker.get_widget_scaling(self)
        self._fonts = {}
        self.title_font = self.tk_font(14, "bold")

        self.rows = []
        self.known_ids = set()
        self.row_backlog = collections.deque()
        self.ui_queue = queue.Queue()
        self.fetch_pool = ThreadPoolExecutor(max_workers=3)
        self.thumb_pool = ThreadPoolExecutor(max_workers=4)
        self.pending_fetches = 0
        self.fetch_summary = collections.Counter()
        self.fetch_errors = []
        self.running = False
        self.cancel_event = threading.Event()
        self.batch = collections.Counter()
        self.batch_rows = []
        self._banner_job = None
        self._bug_dialog_open = False
        # An exception escaping a background thread is a bug: log it and tell the user.
        threading.excepthook = lambda args: self.call_ui(
            self.report_bug, args.exc_type, args.exc_value, args.exc_traceback)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_header()
        self._build_banner()
        self._build_list()
        self._build_footer()
        self.refresh_counts()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_ui_queue)
        self.after(200, self.url_entry.focus)

    # ---------- building the window ----------

    def px(self, n):
        """Logical pixels -> screen pixels (CustomTkinter scales its own widgets; plain Tk doesn't)."""
        return round(n * self.scale)

    def font(self, size, weight="normal"):
        """Font for CustomTkinter widgets (they apply DPI scaling themselves)."""
        key = ("ctk", size, weight)
        if key not in self._fonts:
            self._fonts[key] = ctk.CTkFont(family=self.font_family, size=size, weight=weight)
        return self._fonts[key]

    def tk_font(self, size, weight="normal"):
        """Same font for plain Tk widgets, pre-scaled."""
        key = ("tk", size, weight)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(self, family=self.font_family, size=-self.px(size), weight=weight)
        return self._fonts[key]

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 6))
        header.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, columnspan=2, sticky="w")
        try:
            logo = Image.open(config.LOGO_FILE)
            self.logo_image = ctk.CTkImage(light_image=logo, dark_image=logo, size=(36, 36))
            ctk.CTkLabel(brand, text="", image=self.logo_image).pack(side="left")
        except OSError:
            pass
        ctk.CTkLabel(brand, text=config.APP_NAME, font=self.font(22, "bold"), text_color=TEXT).pack(
            side="left", padx=(10, 0))

        ctk.CTkLabel(header, text=f"วางลิงก์วิดีโอหรือเพลย์ลิสต์ YouTube แล้วกด Enter  "
                                  f"(วางหลายลิงก์พร้อมกันได้ ใส่ได้สูงสุด {MAX_ITEMS} รายการ)",
                     font=self.font(13), text_color=MUTED, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 10))

        self.url_entry = ctk.CTkEntry(
            header, height=44, corner_radius=10, font=self.font(14), fg_color=CARD_BG,
            border_color=CARD_BORDER, text_color=TEXT, placeholder_text_color=MUTED,
            placeholder_text="https://www.youtube.com/watch?v=...")
        self.url_entry.grid(row=2, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda _e: self.add_links())
        self.url_entry.bind("<KP_Enter>", lambda _e: self.add_links())
        self.url_entry.bind("<Control-KeyPress>", self._on_entry_ctrl_key)
        self.url_entry.bind("<Button-3>", self._show_entry_menu)

        self.entry_menu = tk.Menu(self, tearoff=0)
        self.entry_menu.add_command(label="วาง", command=self._paste_into_entry)
        self.entry_menu.add_command(label="ล้างช่อง", command=lambda: self.url_entry.delete(0, "end"))

        ctk.CTkButton(header, text="เพิ่มลงรายการ", width=140, height=44, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, font=self.font(14, "bold"),
                      command=self.add_links).grid(row=2, column=1, padx=(10, 0))

        self.status_label = ctk.CTkLabel(header, text="", font=self.font(12), text_color=MUTED, anchor="w")
        self.status_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_banner(self):
        """Alert strip under the link box for errors and warnings (hidden until needed)."""
        self.banner = ctk.CTkFrame(self, corner_radius=10, border_width=1)
        self.banner.grid_columnconfigure(1, weight=1)
        self.banner_icon = ctk.CTkLabel(self.banner, text="", width=24, font=self.font(16, "bold"))
        self.banner_icon.grid(row=0, column=0, padx=(14, 4), pady=8)
        self.banner_text = ctk.CTkLabel(self.banner, text="", font=self.font(13), text_color=TEXT,
                                        anchor="w", justify="left", wraplength=820)
        self.banner_text.grid(row=0, column=1, sticky="ew", pady=8)
        ctk.CTkButton(self.banner, text="✕", width=28, height=28, corner_radius=8, fg_color="transparent",
                      hover_color=NEUTRAL_HOVER, text_color=MUTED, font=self.font(14),
                      command=self.hide_banner).grid(row=0, column=2, padx=8)

    def _build_list(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(4, 8))
        bar.grid_columnconfigure(0, weight=1)

        self.count_label = ctk.CTkLabel(bar, text="", font=self.font(15, "bold"), text_color=TEXT, anchor="w")
        self.count_label.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(bar, text="ทุกรายการ:", font=self.font(12), text_color=MUTED).grid(
            row=0, column=1, padx=(0, 8))
        self.global_picker = FormatPicker(bar, self, self.default_format, self.apply_format_to_all)
        self.global_picker.grid(row=0, column=2)
        for column, (text, command) in enumerate(
                [("ล้างที่เสร็จแล้ว", self.clear_finished), ("ล้างทั้งหมด", self.clear_rows)], start=3):
            ctk.CTkButton(bar, text=text, width=96, height=30, corner_radius=8,
                          fg_color="transparent", border_width=1, border_color=CARD_BORDER,
                          hover_color=NEUTRAL, text_color=MUTED, font=self.font(12),
                          command=command).grid(row=0, column=column, padx=(10 if column == 3 else 6, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=NEUTRAL,
            scrollbar_button_hover_color=NEUTRAL_HOVER)
        self.list_frame.grid(row=3, column=0, sticky="nsew", padx=(24, 14))
        self.empty_label = ctk.CTkLabel(
            self.list_frame, text=f"ยังไม่มีรายการ\nวางลิงก์ YouTube ด้านบนแล้วกด Enter (สูงสุด {MAX_ITEMS} รายการ)",
            font=self.font(14), text_color=MUTED, justify="center")

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(footer, text="บันทึกไปที่", font=self.font(12), text_color=MUTED).grid(
            row=0, column=0, padx=(24, 10), pady=(14, 0), sticky="w")
        self.dir_label = ctk.CTkLabel(footer, text=self.download_dir, font=self.font(12),
                                      text_color=TEXT, anchor="w")
        self.dir_label.grid(row=0, column=1, sticky="ew", pady=(14, 0))
        for column, (text, command) in enumerate(
                [("เปลี่ยน…", self.choose_folder), ("เปิดโฟลเดอร์", lambda: self.open_folder(self.download_dir))],
                start=2):
            ctk.CTkButton(footer, text=text, width=100, height=30, corner_radius=8, fg_color=NEUTRAL,
                          hover_color=NEUTRAL_HOVER, text_color=TEXT, font=self.font(12),
                          command=command).grid(row=0, column=column, padx=(8, 24 if column == 3 else 0),
                                                pady=(14, 0))

        self.download_button = ctk.CTkButton(
            footer, text="", height=52, corner_radius=12, font=self.font(17, "bold"),
            text_color="white", text_color_disabled=MUTED, command=self.on_download_button)
        self.download_button.grid(row=1, column=0, columnspan=4, sticky="ew", padx=24, pady=(12, 18))

    # ---------- alerts ----------

    def notify(self, message, kind="error", seconds=12):
        """Show the alert banner; it hides itself after `seconds` (0 = until closed)."""
        bg, accent, icon = BANNER_STYLES[kind]
        self.banner.configure(fg_color=bg, border_color=accent)
        self.banner_icon.configure(text=icon, text_color=accent)
        self.banner_text.configure(text=message)
        self.banner.grid(row=1, column=0, sticky="ew", padx=24, pady=(6, 2))
        if self._banner_job:
            self.after_cancel(self._banner_job)
        self._banner_job = self.after(seconds * 1000, self.hide_banner) if seconds else None

    def hide_banner(self):
        self._banner_job = None
        self.banner.grid_remove()

    def show_dialog(self, kind, message):
        # Deferred so a modal dialog never blocks the UI queue that is calling us.
        show = messagebox.showerror if kind == "error" else messagebox.showwarning
        self.after(10, lambda: show(config.APP_NAME, message, parent=self))

    def report_callback_exception(self, exc_type, exc, tb):
        """Tk calls this when a button or event handler raises."""
        self.report_bug(exc_type, exc, tb)

    def report_bug(self, exc_type, exc, tb):
        config.log.error("unexpected error", exc_info=(exc_type, exc, tb))
        if self._bug_dialog_open:  # one dialog at a time, even if errors repeat
            return
        self._bug_dialog_open = True

        def show():
            try:
                messagebox.showerror(
                    config.APP_NAME,
                    f"เกิดข้อผิดพลาดในโปรแกรม\n\n{exc_type.__name__}: {exc}\n\n"
                    f"โปรแกรมยังใช้งานต่อได้ รายละเอียดถูกบันทึกไว้ที่\n{config.LOG_FILE}",
                    parent=self)
            finally:
                self._bug_dialog_open = False

        self.after(10, show)

    # ---------- link entry ----------

    def _on_entry_ctrl_key(self, event):
        # Match the physical key (keycode), so shortcuts also work with the Thai keyboard layout.
        if event.keycode == 86:  # V
            self._paste_into_entry()
            return "break"
        if event.keycode == 65:  # A
            self.url_entry.select_range(0, "end")
            return "break"
        if event.keycode in (67, 88):  # C, X
            event.widget.event_generate("<<Copy>>" if event.keycode == 67 else "<<Cut>>")
            return "break"
        return None

    def _paste_into_entry(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if self.url_entry.select_present():
            self.url_entry.delete("sel.first", "sel.last")
        self.url_entry.insert("insert", " ".join(text.split()))

    def _show_entry_menu(self, event):
        self.entry_menu.tk_popup(event.x_root, event.y_root)

    def set_status(self, text, color=MUTED):
        self.status_label.configure(text=text, text_color=color)

    def capacity_left(self):
        return MAX_ITEMS - len(self.rows) - len(self.row_backlog)

    def add_links(self):
        text = self.url_entry.get()
        links = downloader.split_links(text)
        if not links:
            if text.strip():
                self.notify("ลิงก์ไม่ถูกต้อง — กรุณาวางลิงก์ YouTube เช่น https://www.youtube.com/watch?v=...",
                            "warning")
            return
        if self.capacity_left() <= 0:
            self.notify(f"รายการเต็มแล้ว (สูงสุด {MAX_ITEMS} รายการ) — กด \"ล้างที่เสร็จแล้ว\" "
                        "หรือลบรายการที่ไม่ใช้ก่อน", "warning")
            return
        self.url_entry.delete(0, "end")
        if not self.pending_fetches:
            self.fetch_summary.clear()
            self.fetch_errors.clear()
        for link in links:
            self.pending_fetches += 1
            self.submit(self.fetch_pool, self._fetch_worker, link)
        self._show_fetch_status()

    def _fetch_worker(self, link):
        try:
            items, total = downloader.fetch_entries(link)
        except Exception as exc:
            config.log.warning("fetch failed for %s: %s", link, exc)
            self.call_ui(self._on_fetched, None, 0, downloader.friendly_error(exc))
        else:
            self.call_ui(self._on_fetched, items, total, None)

    def _on_fetched(self, items, total, error):
        self.pending_fetches -= 1
        if error:
            self.fetch_errors.append(error)
            self.notify(f"ดึงข้อมูลจากลิงก์ไม่สำเร็จ: {error}", "error")
            self._show_fetch_status()
            return

        unique = [item for item in items if item["id"] not in self.known_ids]
        room = max(self.capacity_left(), 0)
        new_items, overflow = unique[:room], len(unique) - min(len(unique), room)
        self.known_ids.update(item["id"] for item in new_items)
        self.fetch_summary["added"] += len(new_items)
        self.fetch_summary["skipped"] += len(items) - len(unique)
        self.fetch_summary["overflow"] += overflow
        if overflow:
            self.notify(f"รายการเต็ม {MAX_ITEMS} รายการแล้ว — ไม่ได้เพิ่มอีก {overflow} รายการ", "warning")
        elif total > len(items):
            self.notify(f"เพลย์ลิสต์นี้มี {total} คลิป — ดึงมาแค่ {len(items)} คลิปแรก "
                        f"(ใส่ได้สูงสุด {MAX_ITEMS} รายการ)", "warning")
        if new_items:
            start_building = not self.row_backlog
            self.row_backlog.extend(new_items)
            if start_building:
                self._build_backlog_rows()
        self._show_fetch_status()

    def _show_fetch_status(self):
        s = self.fetch_summary
        parts = []
        if s["added"]:
            parts.append(f"เพิ่ม {s['added']} รายการ")
        if s["skipped"]:
            parts.append(f"ข้าม {s['skipped']} รายการที่มีอยู่แล้ว")
        if s["overflow"]:
            parts.append(f"เกิน {MAX_ITEMS} รายการ {s['overflow']} รายการ")
        if self.fetch_errors:
            parts.append(f"ดึงข้อมูลไม่สำเร็จ {len(self.fetch_errors)} ลิงก์")
        if self.pending_fetches:
            parts.append(f"กำลังดึงข้อมูล {self.pending_fetches} ลิงก์…")
        elif not parts:
            parts.append("ไม่มีรายการใหม่")
        color = ERROR if self.fetch_errors else SUCCESS if s["added"] else MUTED
        self.set_status("   ·   ".join(parts), color)

    def _build_backlog_rows(self):
        for _ in range(min(ROWS_PER_TICK, len(self.row_backlog))):
            item = self.row_backlog.popleft()
            row = ItemRow(self.list_frame, self, item, self.global_picker.get())
            row.pack(fill="x", pady=self.px(5))
            self.rows.append(row)
            if item.get("thumbnail"):
                self.thumb_pool.submit(self._thumb_worker, row, item["thumbnail"])
        self.refresh_counts()
        if self.row_backlog:
            self.after(15, self._build_backlog_rows)  # give Tk time to repaint in between

    def _thumb_worker(self, row, url):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=15) as resp:
                data = resp.read()
            size = (self.px(THUMB_SIZE[0]), self.px(THUMB_SIZE[1]))
            image = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), size, Image.LANCZOS)
        except Exception:
            return  # a missing thumbnail is cosmetic; the row keeps its placeholder
        self.call_ui(row.set_thumbnail, image)

    # ---------- list actions ----------

    def apply_format_to_all(self, value):
        self.default_format = value
        self.settings["default_format"] = value
        config.save_settings(self.settings)
        for row in self.rows:
            row.set_format(value)
        self.refresh_counts()

    def remove_row(self, row):
        if row.state in ACTIVE_STATES:
            return
        self.rows.remove(row)
        self.known_ids.discard(row.item["id"])
        row.destroy()
        self.refresh_counts()

    def clear_finished(self):
        for row in [r for r in self.rows if r.state == "done"]:
            self.remove_row(row)

    def clear_rows(self):
        for item in self.row_backlog:
            self.known_ids.discard(item["id"])
        self.row_backlog.clear()
        for row in list(self.rows):
            self.remove_row(row)
        if not self.running:
            self.set_status("")

    def choose_folder(self):
        initial = self.download_dir if os.path.isdir(self.download_dir) else os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self, initialdir=initial,
                                       title="เลือกโฟลเดอร์สำหรับบันทึกไฟล์")
        if not path:
            return
        path = os.path.normpath(path)
        problem = folder_problem(path)
        if problem:
            self.notify(f"บันทึกไฟล์ลงโฟลเดอร์นี้ไม่ได้ ({problem}) — กรุณาเลือกโฟลเดอร์อื่น", "error")
            return
        self.download_dir = path
        self.dir_label.configure(text=path)
        self.settings["download_dir"] = path
        config.save_settings(self.settings)

    def open_folder(self, path):
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except OSError as exc:
            self.notify(f"เปิดโฟลเดอร์ไม่ได้: {exc.strerror or exc}", "error")

    def reveal_file(self, path):
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            self.notify("ไม่พบไฟล์แล้ว (อาจถูกย้ายหรือลบ) — เปิดโฟลเดอร์ให้แทน", "warning")
            self.open_folder(os.path.dirname(path) if path else self.download_dir)

    def refresh_counts(self):
        total = len(self.rows)
        done = sum(row.state == "done" for row in self.rows)
        self.count_label.configure(
            text=f"รายการ ({total}/{MAX_ITEMS})" + (f"  ·  เสร็จแล้ว {done}" if done else ""),
            text_color=WARNING if total >= MAX_ITEMS else TEXT)
        if total or self.row_backlog:
            self.empty_label.pack_forget()
        else:
            self.empty_label.pack(pady=90)

        if self.running:
            stopping = self.cancel_event.is_set()
            self.download_button.configure(
                state="disabled" if stopping else "normal", fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
                text="กำลังหยุด…" if stopping else
                f"หยุดดาวน์โหลด   ({self.batch['finished']}/{self.batch['total']} เสร็จแล้ว)")
        else:
            pending = sum(row.state in PENDING_STATES for row in self.rows)
            self.download_button.configure(
                state="normal" if pending else "disabled",
                fg_color=ACCENT if pending else NEUTRAL, hover_color=ACCENT_HOVER,
                text=f"ดาวน์โหลดทั้งหมด  ({pending} รายการ)")

    # ---------- downloading ----------

    def on_download_button(self):
        if self.running:
            self.cancel_event.set()
            self.refresh_counts()
        else:
            self.start_downloads()

    def start_downloads(self):
        targets = [row for row in self.rows if row.state in PENDING_STATES]
        if not targets:
            return
        problem = folder_problem(self.download_dir)
        if problem:
            self.show_dialog("error", f"บันทึกไฟล์ลงโฟลเดอร์นี้ไม่ได้\n{self.download_dir}\n\n({problem})\n\n"
                                      "กด \"เปลี่ยน…\" ด้านล่างเพื่อเลือกโฟลเดอร์อื่น")
            return
        self.hide_banner()
        self.running = True
        self.cancel_event = threading.Event()
        self.batch = collections.Counter(total=len(targets))
        self.batch_rows = targets
        pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS)
        for row in targets:
            row.set_state("queued")
            self.submit(pool, self._download_worker, row, row.format_picker.get(), self.download_dir,
                        self.cancel_event)
        pool.shutdown(wait=False)
        self.set_status(f"เริ่มดาวน์โหลด {len(targets)} รายการ…")
        self.refresh_counts()

    def _download_worker(self, row, fmt, out_dir, cancel_event):
        if cancel_event.is_set():
            self.call_ui(self._on_download_finished, row, "cancelled", None)
            return
        self.call_ui(row.set_state, "downloading")
        last_update = [0.0]

        def on_progress(fraction, speed, eta, phase):
            now = time.monotonic()
            if phase == "download" and now - last_update[0] < 0.2:
                return
            last_update[0] = now
            self.call_ui(row.show_progress, fraction, speed, eta, phase)

        url = row.item["url"]
        try:
            path = downloader.download(url, fmt, out_dir, on_progress, cancel_event)
        except downloader.DownloadCancelled:
            self.call_ui(self._on_download_finished, row, "cancelled", None)
        except Exception as exc:
            if isinstance(exc, downloader.DownloadError):
                config.log.warning("download failed: %s [%s]: %s", url, fmt, exc)
            else:  # not a yt-dlp error, so likely a bug: keep the traceback
                config.log.exception("download crashed: %s [%s]", url, fmt)
            self.call_ui(self._on_download_finished, row, "error", downloader.friendly_error(exc))
        else:
            self.call_ui(self._on_download_finished, row, "done", path)

    def _on_download_finished(self, row, state, detail):
        if row.winfo_exists():
            row.set_state(state, detail)
        self.batch["finished"] += 1
        self.batch[state] += 1
        if self.batch["finished"] >= self.batch["total"]:
            self.running = False
            self._report_batch()
        self.refresh_counts()

    def _report_batch(self):
        ok, total = self.batch["done"], self.batch["total"]
        failed = [row for row in self.batch_rows if row.state == "error"]
        if self.batch["cancelled"]:
            self.set_status(f"หยุดดาวน์โหลดแล้ว — สำเร็จ {ok} รายการ")
        elif failed:
            self.set_status(f"ดาวน์โหลดสำเร็จ {ok}/{total} รายการ — ผิดพลาด {len(failed)} รายการ", ERROR)
            lines = [f"• {fit_text(row.item['title'], self.title_font, self.px(320))}\n   {row.error}"
                     for row in failed[:8]]
            if len(failed) > 8:
                lines.append(f"…และอีก {len(failed) - 8} รายการ")
            self.show_dialog("warning", f"ดาวน์โหลดไม่สำเร็จ {len(failed)} จาก {total} รายการ\n\n"
                                        + "\n".join(lines)
                                        + "\n\nกด \"ดาวน์โหลดทั้งหมด\" อีกครั้งเพื่อลองใหม่เฉพาะรายการที่ไม่สำเร็จ")
        else:
            self.set_status(f"✓ ดาวน์โหลดเสร็จทั้งหมด {ok} รายการ", SUCCESS)
            self.notify(f"ดาวน์โหลดเสร็จทั้งหมด {ok} รายการ", "success", seconds=6)

    # ---------- plumbing ----------

    def submit(self, pool, fn, *args):
        """Run fn on a pool thread; an exception it lets escape is reported instead of lost."""
        pool.submit(fn, *args).add_done_callback(self._check_future)

    def _check_future(self, future):
        if not future.cancelled() and future.exception() is not None:
            exc = future.exception()
            self.call_ui(self.report_bug, type(exc), exc, exc.__traceback__)

    def call_ui(self, fn, *args):
        """Worker threads must not touch Tk; they queue calls for the UI thread instead."""
        self.ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        while True:
            try:
                fn, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                self.report_bug(*sys.exc_info())
        self.after(60, self._drain_ui_queue)

    def _on_close(self):
        if self.running and not messagebox.askyesno(
                config.APP_NAME, "กำลังดาวน์โหลดอยู่ ต้องการปิดโปรแกรมหรือไม่?", parent=self):
            return
        self.cancel_event.set()
        self.destroy()
        os._exit(0)  # don't wait for worker threads still finishing a download


def main():
    ctk.set_appearance_mode("dark")
    App().mainloop()


if __name__ == "__main__":  # development shortcut: skips the startup check in main.py
    config.setup_logging()
    main()
