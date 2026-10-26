"""Paths, settings and logging shared by the startup check and the app.

Standard library only: the startup check imports this before it knows whether the
app's packages are installed.
"""
import json
import logging
import os
import sys

APP_NAME = "YouTube Downloader"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LOG_FILE = os.path.join(APP_DIR, "error.log")
REQUIREMENTS_FILE = os.path.join(APP_DIR, "requirements.txt")
ICON_FILE = os.path.join(APP_DIR, "ytmp4.ico")
LOGO_FILE = os.path.join(APP_DIR, "ytmp4.png")
DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "YouTube Downloads")
MAX_LOG_BYTES = 1024 * 1024

log = logging.getLogger("ytdownloader")


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("could not save settings")


def download_dir(settings):
    return os.path.normpath(settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR)


def setup_logging():
    """Append errors to error.log (rotated at 1 MB). Under pythonw there is no console,
    so stray prints and tracebacks go there too instead of crashing on a None stream."""
    if log.handlers:
        return
    try:
        if os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".old")
    except OSError:
        pass
    try:
        stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = stream
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
