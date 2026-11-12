import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from demo_tree import build_demo
from server import Handler


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        build_demo(cls.root)
        os.environ["FILES_DEMO"] = str(cls.root)
        os.environ["FILES_ALLOW_ALL"] = "1"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    def json(self, method, path, payload=None):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        status, data = self.request(method, path, body, headers)
        return status, json.loads(data.decode() or "{}")

    def test_page_and_places(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Files", page)
        status, css = self.request("GET", "/static/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b"--accent", css)
        status, data = self.json("GET", "/api/places")
        self.assertEqual(status, 200)
        ids = {place["id"] for place in data["places"]}
        self.assertIn("config", ids)
        self.assertIn("disk-POOL-CAM", ids)
        self.assertIn("host", ids)
        host = next(place for place in data["places"] if place["id"] == "host")
        self.assertTrue(host["available"])

    def test_rejects_callers_outside_ingress(self):
        os.environ["FILES_ALLOW_ALL"] = "0"
        try:
            status, data = self.json("GET", "/api/places")
        finally:
            os.environ["FILES_ALLOW_ALL"] = "1"
        self.assertEqual(status, 403)
        self.assertIn("sidebar", data["error"])

    def test_path_cannot_escape(self):
        status, data = self.json("GET", "/api/list?place=config&path=../../etc/passwd")
        self.assertEqual(status, 403)
        self.assertFalse((self.root.parent / "passwd").exists())

    def test_edit_upload_and_folder(self):
        status, data = self.json("GET", "/api/list?place=config&path=packages")
        self.assertEqual(status, 200)
        self.assertIn("irrigation.yaml", [item["name"] for item in data["entries"]])
        self.assertTrue(data["display"].endswith("/config/packages"))
        status, text = self.request("GET", "/api/text?place=config&path=packages/irrigation.yaml")
        self.assertIn(b"Automatic", text)
        status, data = self.request(
            "POST",
            "/api/text?place=config&path=packages/irrigation.yaml",
            body=b"input_boolean:\n  irrigation_automatic:\n    name: Changed\n",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Changed", (self.root / "config/packages/irrigation.yaml").read_bytes())
        status, _data = self.request(
            "POST",
            "/api/upload?place=share&path=&name=photo.bin",
            body=b"\x00\x01raw",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 200)
        self.assertEqual((self.root / "share/photo.bin").read_bytes(), b"\x00\x01raw")
        status, data = self.json("POST", "/api/mkdir", {"place": "share", "path": "", "name": "logs"})
        self.assertEqual(status, 200)
        self.assertTrue((self.root / "share/logs").is_dir())
        status, data = self.json(
            "POST",
            "/api/rename",
            {"place": "share", "path": "readme.txt", "name": "about.txt"},
        )
        self.assertEqual(status, 200)
        self.assertTrue((self.root / "share/about.txt").is_file())

    def test_copy_and_refuse_host_root_delete(self):
        status, data = self.json(
            "POST",
            "/api/transfer",
            {
                "op": "copy",
                "from_place": "config",
                "paths": ["packages/irrigation.yaml"],
                "to_place": "backup",
                "to_path": "",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertTrue((self.root / "backup/irrigation.yaml").is_file())
        status, data = self.json("POST", "/api/delete", {"place": "host", "paths": ["etc"]})
        self.assertEqual(status, 403)
        self.assertTrue((self.root / "places/host/etc/hostname").is_file())
        scratch = self.root / "places/host/etc/scratch.txt"
        scratch.write_text("temp\n", encoding="utf-8")
        status, data = self.json("POST", "/api/delete", {"place": "host", "paths": ["etc/scratch.txt"]})
        self.assertEqual(status, 200)
        self.assertFalse(scratch.exists())

    def test_archive_extract_blocks_zip_slip(self):
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("../escaped.txt", "nope")
        (self.root / "config/bad.zip").write_bytes(bad.getvalue())
        status, data = self.json("POST", "/api/extract", {"place": "config", "path": "bad.zip"})
        self.assertEqual(status, 400)
        self.assertFalse((self.root / "escaped.txt").exists())
        good = io.BytesIO()
        with zipfile.ZipFile(good, "w") as archive:
            archive.writestr("nested/hi.txt", "hello")
        (self.root / "config/good.zip").write_bytes(good.getvalue())
        status, data = self.json("POST", "/api/extract", {"place": "config", "path": "good.zip"})
        self.assertEqual(status, 200)
        self.assertEqual((self.root / "config/nested/hi.txt").read_text(encoding="utf-8"), "hello")

    def test_zip_download_and_search(self):
        status, data = self.request(
            "POST",
            "/api/zip",
            body=json.dumps({"place": "config", "paths": ["packages"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, data[:200])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIn("packages/irrigation.yaml", archive.namelist())
        status, found = self.json("GET", "/api/search?place=config&path=&q=irrigation&hidden=1")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["path"].endswith("irrigation.yaml") for item in found["entries"]))

    def test_static_cannot_escape(self):
        status, _data = self.request("GET", "/static/../server.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
