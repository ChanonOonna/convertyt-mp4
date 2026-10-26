"""yt-dlp wrapper: list the videos behind a link and download them as MP4/MP3."""
import functools
import os
import re
import shutil
import sys

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

# Display label -> what to download. The order here is the order shown in the UI.
FORMATS = {
    "MP4 1080p": {"kind": "video", "height": 1080},
    "MP4 720p": {"kind": "video", "height": 720},
    "MP4 480p": {"kind": "video", "height": 480},
    "MP3": {"kind": "audio"},
}
DEFAULT_FORMAT = "MP4 1080p"
MAX_ITEMS = 50  # the download list holds at most this many, so never fetch more from a playlist

_UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]", "[Unavailable video]"}


class _SilentLogger:
    """The app runs under pythonw (no console), so yt-dlp must never print."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


@functools.cache
def find_ffmpeg():
    """ffmpeg is needed to merge 1080p video+audio and to make MP3s."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


@functools.cache
def find_js_runtimes():
    """YouTube needs a JS runtime to unlock most formats. Returns {name: {"path": exe}}
    for each one found on PATH, plus a Deno that the startup check pip-installed into .venv."""
    scripts_dir = os.path.dirname(sys.executable)
    runtimes = {}
    for name in ("deno", "node", "bun"):
        exe = shutil.which(name) or shutil.which(name, path=scripts_dir)
        if exe:
            runtimes[name] = {"path": exe}
    return runtimes


def _base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "noplaylist": True,
        "js_runtimes": find_js_runtimes() or {"deno": {}},
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "trim_file_name": 150,  # keep long titles under Windows' path length limit
    }


def split_links(text):
    """Split pasted text into URLs; bare 'youtube.com/...' gets an https:// prefix."""
    links = []
    for piece in re.split(r"[\s,;]+", text or ""):
        piece = piece.strip().strip("<>\"'")
        if not piece:
            continue
        if not re.match(r"^https?://", piece, re.I):
            if not re.match(r"^(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", piece, re.I):
                continue
            piece = "https://" + piece
        if piece not in links:
            links.append(piece)
    return links


def format_duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def friendly_error(exc):
    """Turn a yt-dlp exception into a short Thai message."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", msg)
    known = [  # first match wins
        ("Private video", "วิดีโอนี้เป็นวิดีโอส่วนตัว"),
        ("live event will begin", "ไลฟ์นี้ยังไม่เริ่มถ่ายทอด"),
        ("unavailable", "วิดีโอนี้ไม่พร้อมใช้งาน"),
        ("confirm your age", "วิดีโอจำกัดอายุ ต้องล็อกอินจึงจะโหลดได้"),
        ("not a bot", "YouTube ขอยืนยันตัวตน ลองใหม่อีกครั้งภายหลัง"),
        ("members-only", "วิดีโอสำหรับสมาชิกช่องเท่านั้น"),
        ("Requested format is not available", "คลิปนี้ไม่มีรูปแบบที่เลือก ลองเลือกรูปแบบอื่น"),
        ("HTTP Error 403", "YouTube ปฏิเสธการดาวน์โหลด (403) ปิดแล้วเปิดโปรแกรมใหม่เพื่ออัปเดต yt-dlp"),
        ("HTTP Error 429", "YouTube จำกัดการโหลดชั่วคราว (429) รอสักพักแล้วลองใหม่"),
        ("Unsupported URL", "ลิงก์นี้ไม่รองรับ"),
        ("is not a valid URL", "ลิงก์ไม่ถูกต้อง"),
        ("getaddrinfo failed", "เชื่อมต่ออินเทอร์เน็ตไม่ได้"),
        ("Failed to resolve", "เชื่อมต่ออินเทอร์เน็ตไม่ได้"),
        ("timed out", "การเชื่อมต่อหมดเวลา"),
        ("No space left", "พื้นที่ดิสก์เต็ม"),
        ("Errno 28", "พื้นที่ดิสก์เต็ม"),
        ("Permission denied", "ไม่มีสิทธิ์เขียนไฟล์ในโฟลเดอร์ปลายทาง"),
        ("Errno 13", "ไม่มีสิทธิ์เขียนไฟล์ในโฟลเดอร์ปลายทาง"),
        ("ffmpeg not found", "ไม่พบ ffmpeg ปิดแล้วเปิดโปรแกรมใหม่เพื่อให้ระบบติดตั้ง"),
        ("ffmpeg is not installed", "ไม่พบ ffmpeg ปิดแล้วเปิดโปรแกรมใหม่เพื่อให้ระบบติดตั้ง"),
        ("ffmpeg", "รวม/แปลงไฟล์ไม่สำเร็จ (ffmpeg)"),
    ]
    for needle, thai in known:
        if needle.lower() in msg.lower():
            return thai
    first_line = msg.splitlines()[0] if msg else "ไม่ทราบสาเหตุ"
    return first_line[:160]


def _is_youtube(entry):
    key = entry.get("ie_key") or entry.get("extractor_key") or ""
    return key.lower().startswith("youtube")


def _to_item(entry):
    vid = entry.get("id")
    youtube = _is_youtube(entry) and vid and len(vid) == 11
    if youtube:
        url = f"https://www.youtube.com/watch?v={vid}"
        thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
    else:
        url = entry.get("webpage_url") or entry.get("url")
        thumbs = entry.get("thumbnails") or []
        thumb = entry.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None)
    return {
        "id": vid or url,
        "url": url,
        "title": entry.get("title") or url,
        "channel": entry.get("channel") or entry.get("uploader") or "",
        "duration": entry.get("duration"),
        "thumbnail": thumb,
    }


def fetch_entries(url):
    """Return (items, total): the downloadable videos behind a link (one for a video, up to
    MAX_ITEMS for a playlist) and how many videos the playlist really has."""
    opts = {**_base_opts(), "extract_flat": "in_playlist", "playlistend": MAX_ITEMS}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") in ("playlist", "multi_video") or "entries" in info:
        entries = [
            e for e in (info.get("entries") or [])
            if e
            and e.get("ie_key") != "YoutubeTab"  # channel tabs are sub-playlists, not videos
            and e.get("_type") != "playlist"
            and e.get("title") not in _UNAVAILABLE_TITLES
        ]
    else:
        entries = [info]

    items = [_to_item(e) for e in entries if e.get("id") or e.get("url")]
    if not items:
        raise DownloadError("ไม่พบวิดีโอในลิงก์นี้")
    return items, max(info.get("playlist_count") or 0, len(items))


def _format_opts(fmt):
    spec = FORMATS[fmt]
    if spec["kind"] == "audio":
        return {
            "format": "ba/b",
            "outtmpl": "%(title)s.%(ext)s",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegMetadata"},
            ],
        }
    height = spec["height"]
    return {
        "format": "bv*+ba/b",
        # Largest resolution up to `height`, preferring H.264/AAC so the MP4 plays everywhere.
        "format_sort": [f"res:{height}", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"],
        "merge_output_format": "mp4",
        # The resolution goes in the name so a 480p copy never blocks a later 1080p download.
        "outtmpl": "%(title)s (%(height|NA)sp).%(ext)s",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    }


def download(url, fmt, out_dir, on_progress=None, cancel_event=None):
    """Download one video and return the final file path.

    on_progress(fraction_or_None, speed_bytes_per_s, eta_seconds, phase) is called from
    this (worker) thread; phase is "download" or "convert". Setting cancel_event aborts
    the download with DownloadCancelled.
    """
    opts = {**_base_opts(), **_format_opts(fmt), "paths": {"home": out_dir}}
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg

    # Sizes of the streams yt-dlp will fetch (video + audio for MP4), so progress
    # can be reported as one 0-100% bar instead of restarting for each stream.
    part_sizes = {}
    part_order = []

    def overall_fraction(d):
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        current = (d.get("info_dict") or {}).get("format_id")
        if len(part_order) > 1 and current in part_sizes and all(part_sizes.values()):
            before = sum(part_sizes[f] for f in part_order[:part_order.index(current)])
            return min(1.0, (before + done) / sum(part_sizes.values()))
        if len(part_order) > 1 and current in part_order and total:
            return (part_order.index(current) + done / total) / len(part_order)
        return done / total if total else None

    def progress_hook(d):
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        if on_progress and d["status"] == "downloading":
            on_progress(overall_fraction(d), d.get("speed"), d.get("eta"), "download")

    def postprocessor_hook(d):
        if on_progress and d["status"] == "started":
            on_progress(None, None, None, "convert")

    opts["progress_hooks"] = [progress_hook]
    opts["postprocessor_hooks"] = [postprocessor_hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info.get("is_live"):  # would record forever
            raise DownloadError("คลิปนี้เป็นไลฟ์สดที่กำลังถ่ายทอดอยู่ ยังโหลดไม่ได้")
        for part in info.get("requested_formats") or [info]:
            part_order.append(part.get("format_id"))
            part_sizes[part.get("format_id")] = part.get("filesize") or part.get("filesize_approx")
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        info = ydl.process_ie_result(info, download=True)

    downloads = info.get("requested_downloads") or []
    path = (downloads[-1].get("filepath") if downloads else None) or info.get("filepath")
    if not path or not os.path.exists(path):
        raise DownloadError("ดาวน์โหลดเสร็จแต่ไม่พบไฟล์ปลายทาง")
    return path
