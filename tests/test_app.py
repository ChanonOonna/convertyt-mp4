import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import app, parse_urls


class DownloaderAppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_homepage_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("โหลด YouTube เป็น MP4", response.get_data(as_text=True))

    def test_download_requires_urls(self):
        response = self.client.post("/download", json={})
        self.assertEqual(response.status_code, 400)

    def test_parse_urls_supports_multiple_delimiters(self):
        parsed = parse_urls("https://a.com\nhttps://b.com, https://c.com;https://d.com")
        self.assertEqual(parsed, ["https://a.com", "https://b.com", "https://c.com", "https://d.com"])

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
