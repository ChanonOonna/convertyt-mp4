import difflib
import os
import re
import subprocess
import sys
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import yt_dlp

PORT = 5000
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "YouTube Downloads")
CURRENT_DOWNLOAD_DIR = DOWNLOAD_DIR
os.makedirs(CURRENT_DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=".")
CORS(app)

jobs = {}
jobs_lock = threading.Lock()


def sanitize_filename(name: str) -> str:
    safe = re.sub(r"[^\w\-. ]+", "_", name or "video")
    safe = re.sub(r"\s+", " ", safe).strip() or "video"
    return safe[:180]


def parse_urls(raw_text: str):
    if not raw_text:
        return []
    pieces = re.split(r"[\r\n,;]+", raw_text)
    urls = []
    for piece in pieces:
        cleaned = piece.strip()
        if cleaned:
            urls.append(cleaned)
    return urls


def normalize_name(name: str) -> str:
    return ''.join(ch for ch in (name or '').lower() if ch.isalnum())


def get_download_dir():
    return CURRENT_DOWNLOAD_DIR


def find_best_download_match(expected: str, download_dir: str | None = None):
    expected_key = normalize_name(expected)
    if not expected_key:
        return None

    download_dir = download_dir or get_download_dir()
    # Try exact normalized substring matching first.
    for f in os.listdir(download_dir):
        candidate_key = normalize_name(os.path.splitext(f)[0])
        if not candidate_key:
            continue
        if expected_key in candidate_key or candidate_key in expected_key:
            return f

    best = None
    best_score = 0.0
    for f in os.listdir(download_dir):
        candidate_key = normalize_name(os.path.splitext(f)[0])
        if not candidate_key:
            continue
        score = difflib.SequenceMatcher(None, expected_key, candidate_key).ratio()
        if score > best_score:
            best_score = score
            best = f

    return best if best_score >= 0.35 else None


def open_path(path: str):
    if hasattr(os, "startfile"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def download_video(job_id: str, url: str, quality: str = "best", fmt: str = "mp4"):
    def progress_hook(d):
        with jobs_lock:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                pct = int(downloaded / total * 100) if total else 0
                jobs[job_id]["progress"] = pct
            elif d["status"] == "finished":
                jobs[job_id]["progress"] = 99

    quality_formats = {
        "best": "best",
        "720p": "best[height<=720]",
        "480p": "best[height<=480]",
        "360p": "best[height<=360]",
    }

    is_audio = fmt == "mp3"
    target_ext = "mp3" if is_audio else "mp4"

    download_dir = get_download_dir()
    os.makedirs(download_dir, exist_ok=True)

    ydl_opts = {
        "format": "bestaudio/best" if is_audio else quality_formats.get(quality, "best"),
        "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }

    if is_audio:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with jobs_lock:
            jobs[job_id].update(status="downloading", progress=0, error="", filename="", title="")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Obtain the actual filename ytdlp created and normalize to the target extension
            try:
                prepared = ydl.prepare_filename(info)
                base, _ = os.path.splitext(prepared)
                final_path = base + "." + target_ext
                actual_filename = os.path.basename(final_path)
            except Exception:
                # Fallback to sanitized title
                title = sanitize_filename(info.get("title", "video") if isinstance(info, dict) else "video")
                actual_filename = f"{title}.{target_ext}"
                final_path = os.path.join(download_dir, actual_filename)

            # If the exact file doesn't exist, try to find a close match in the download directory.
            if not os.path.exists(os.path.join(download_dir, actual_filename)):
                found = find_best_download_match(actual_filename, download_dir)
                if found:
                    actual_filename = found

            with jobs_lock:
                jobs[job_id].update(status="done", title=info.get("title", ""), filename=actual_filename, progress=100)
    except Exception as exc:
        with jobs_lock:
            jobs[job_id].update(status="error", error=str(exc).strip() or "ไม่ทราบสาเหตุ")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/health")
def health():
    with jobs_lock:
        active_jobs = sum(1 for job in jobs.values() if job["status"] in {"pending", "downloading"})
        completed_jobs = sum(1 for job in jobs.values() if job["status"] == "done")
    return jsonify({
        "status": "ok",
        "download_dir": get_download_dir(),
        "job_count": len(jobs),
        "active_jobs": active_jobs,
        "completed_jobs": completed_jobs,
        "port": PORT,
    })


@app.route("/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    raw_urls = data.get("urls") or data.get("url") or ""

    if isinstance(raw_urls, str):
        urls = parse_urls(raw_urls)
    elif isinstance(raw_urls, list):
        urls = [str(item).strip() for item in raw_urls if str(item).strip()]
    else:
        urls = []

    quality = str(data.get("quality", "best") or "best")
    fmt = str(data.get("format", "mp4") or "mp4").lower()
    if fmt not in ("mp3", "mp4"):
        fmt = "mp4"
    if not urls:
        return jsonify({"error": "ไม่มีลิงก์ให้ดาวน์โหลด"}), 400

    created_jobs = []
    for url in urls:
        job_id = str(uuid.uuid4())
        with jobs_lock:
            jobs[job_id] = {
                "status": "pending",
                "url": url,
                "title": "",
                "filename": "",
                "error": "",
                "progress": 0,
                "quality": quality,
                "format": fmt,
            }
        threading.Thread(target=download_video, args=(job_id, url, quality, fmt), daemon=True).start()
        created_jobs.append({"job_id": job_id, "url": url, "quality": quality, "format": fmt})

    return jsonify({"jobs": created_jobs, "download_dir": get_download_dir()})


@app.route("/status/<job_id>")
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/file/<filename>")
def serve_file(filename):
    return send_from_directory(get_download_dir(), filename, as_attachment=True)


@app.route("/open_folder")
def open_folder():
    open_path(get_download_dir())
    return jsonify({"ok": True, "download_dir": get_download_dir()})


@app.route("/browse_folder")
def browse_folder():
    if sys.platform != "win32":
        return jsonify({"error": "การเลือกโฟลเดอร์ผ่านหน้าต่างระบบรองรับเฉพาะ Windows"}), 400

    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        f"$f.SelectedPath = '{get_download_dir()}'; "
        "$f.Description = 'เลือกโฟลเดอร์ปลายทางสำหรับบันทึกไฟล์'; "
        "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        return jsonify({"error": f"ไม่สามารถเปิดหน้าต่างเลือกโฟลเดอร์ได้: {exc}"}), 500

    selected = (result.stdout or "").strip()
    if not selected:
        return jsonify({"cancelled": True})
    return jsonify({"download_dir": selected})


@app.route("/set_download_dir", methods=["POST"])
def set_download_dir():
    data = request.get_json(silent=True) or {}
    requested_dir = str(data.get("download_dir", "") or "").strip()
    if not requested_dir:
        return jsonify({"error": "ไม่มีโฟลเดอร์ปลายทาง"}), 400
    try:
        new_dir = os.path.abspath(requested_dir)
        os.makedirs(new_dir, exist_ok=True)
    except Exception as exc:
        return jsonify({"error": f"โฟลเดอร์ไม่ถูกต้อง: {exc}"}), 400
    global CURRENT_DOWNLOAD_DIR
    CURRENT_DOWNLOAD_DIR = new_dir
    return jsonify({"download_dir": CURRENT_DOWNLOAD_DIR})


@app.route('/resync')
def resync_filenames():
    updated = 0
    download_dir = get_download_dir()
    with jobs_lock:
        for jid, job in list(jobs.items()):
            if job.get('status') == 'done':
                fn = job.get('filename') or ''
                if not fn:
                    continue
                expected = os.path.join(download_dir, fn)
                if os.path.exists(expected):
                    continue
                found = find_best_download_match(fn, download_dir)
                if found:
                    jobs[jid]['filename'] = found
                    updated += 1
    return jsonify({'updated': updated})


@app.route("/clear_jobs")
def clear_jobs():
    with jobs_lock:
        cleared = [job_id for job_id, job in jobs.items() if job["status"] in {"done", "error"}]
        for job_id in cleared:
            del jobs[job_id]
    return jsonify({"cleared": len(cleared)})


@app.route("/jobs")
def all_jobs():
    with jobs_lock:
        return jsonify(dict(jobs))


if __name__ == "__main__":
    print(f"YouTube Downloader running at http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
