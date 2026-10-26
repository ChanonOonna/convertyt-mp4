import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import downloader


class SplitLinksTests(unittest.TestCase):
    def test_splits_on_whitespace_commas_and_newlines(self):
        text = "https://youtu.be/a1\nhttps://youtu.be/b2, https://youtu.be/c3;https://youtu.be/d4"
        self.assertEqual(downloader.split_links(text), [
            "https://youtu.be/a1", "https://youtu.be/b2", "https://youtu.be/c3", "https://youtu.be/d4"])

    def test_adds_scheme_to_bare_youtube_links_and_drops_junk(self):
        self.assertEqual(downloader.split_links("youtu.be/abc hello www.youtube.com/watch?v=x"),
                         ["https://youtu.be/abc", "https://www.youtube.com/watch?v=x"])

    def test_removes_duplicates(self):
        self.assertEqual(downloader.split_links("https://youtu.be/a https://youtu.be/a"),
                         ["https://youtu.be/a"])


class FormattingTests(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(downloader.format_duration(None), "")
        self.assertEqual(downloader.format_duration(19), "0:19")
        self.assertEqual(downloader.format_duration(213.0), "3:33")
        self.assertEqual(downloader.format_duration(3725), "1:02:05")

    def test_friendly_error_translates_known_messages(self):
        err = Exception("\x1b[0;31mERROR:\x1b[0m [youtube] abc: Private video. Sign in if you've been granted access")
        self.assertEqual(downloader.friendly_error(err), "วิดีโอนี้เป็นวิดีโอส่วนตัว")
        self.assertEqual(downloader.friendly_error(Exception("ERROR: [youtube] x: This video is unavailable")),
                         "วิดีโอนี้ไม่พร้อมใช้งาน")

    def test_friendly_error_keeps_first_line_of_unknown_messages(self):
        self.assertEqual(downloader.friendly_error(Exception("ERROR: something odd\ndetails")), "something odd")

    def test_friendly_error_tells_missing_ffmpeg_from_failed_ffmpeg(self):
        missing = Exception("ERROR: You have requested merging of multiple formats but ffmpeg is not installed")
        self.assertIn("ไม่พบ ffmpeg", downloader.friendly_error(missing))
        failed = Exception("ERROR: Postprocessing: ffmpeg exited with code 1")
        self.assertIn("แปลงไฟล์ไม่สำเร็จ", downloader.friendly_error(failed))

    def test_friendly_error_disk_and_permission_problems(self):
        self.assertEqual(downloader.friendly_error(OSError(28, "No space left on device")), "พื้นที่ดิสก์เต็ม")
        self.assertEqual(downloader.friendly_error(PermissionError(13, "Permission denied")),
                         "ไม่มีสิทธิ์เขียนไฟล์ในโฟลเดอร์ปลายทาง")


class FormatOptionTests(unittest.TestCase):
    def test_every_format_builds_options(self):
        for fmt in downloader.FORMATS:
            self.assertIn("format", downloader._format_opts(fmt))

    def test_video_formats_cap_resolution_and_output_mp4(self):
        opts = downloader._format_opts("MP4 720p")
        self.assertEqual(opts["format_sort"][0], "res:720")
        self.assertEqual(opts["merge_output_format"], "mp4")

    def test_mp3_extracts_audio(self):
        pp = downloader._format_opts("MP3")["postprocessors"][0]
        self.assertEqual((pp["key"], pp["preferredcodec"]), ("FFmpegExtractAudio", "mp3"))


class ItemConversionTests(unittest.TestCase):
    def test_flat_youtube_entry_gets_watch_url_and_thumbnail(self):
        item = downloader._to_item({"id": "jNQXAC9IVRw", "ie_key": "Youtube", "title": "Me at the zoo",
                                    "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "duration": 19,
                                    "channel": "jawed"})
        self.assertEqual(item["url"], "https://www.youtube.com/watch?v=jNQXAC9IVRw")
        self.assertEqual(item["thumbnail"], "https://i.ytimg.com/vi/jNQXAC9IVRw/mqdefault.jpg")
        self.assertEqual((item["title"], item["channel"], item["duration"]), ("Me at the zoo", "jawed", 19))


if __name__ == "__main__":
    unittest.main()
