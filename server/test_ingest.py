"""Tests for the ingest server. Standard library only:  python -m unittest server/test_ingest.py"""
import json
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import ingest


def wav(seconds: float = 2.0, rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    n = int(seconds * rate * channels * bits // 8)
    hdr = (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
           rate * channels * bits // 8, channels * bits // 8, bits) + b"data" + struct.pack("<I", n))
    return hdr + b"\x01\x00" * (n // 2)


class IngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.httpd = ingest.serve(Path(cls.tmp.name), "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.store.db.close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def register(self):
        code, j = self.call("POST", "/v1/register", json.dumps({"consent_version": "1"}).encode(),
                            {"Content-Type": "application/json"})
        self.assertEqual(code, 200)
        return j["install_id"], {"Authorization": "Bearer " + j["token"]}

    def test_health(self):
        self.assertEqual(self.call("GET", "/healthz"), (200, {"ok": True}))

    def test_register_needs_consent_version(self):
        self.assertEqual(self.call("POST", "/v1/register", b"{}")[0], 400)

    def test_upload_and_count(self):
        _iid, auth = self.register()
        meta = json.dumps({"mode": "HINGLISH", "raw_text": "namaste", "final_text": "Namaste.", "evil": "dropped"})
        code, j = self.call("POST", "/v1/samples", wav(2.0), {**auth, "X-Meta": meta})
        self.assertEqual(code, 200, j)
        self.assertEqual(self.call("GET", "/v1/me", headers=auth)[1], {"samples": 1})
        db = ingest.sqlite3.connect(str(Path(self.tmp.name) / "meta.db"))
        row = db.execute("SELECT meta FROM samples WHERE id=?", (j["sample_id"],)).fetchone()
        db.close()
        stored = json.loads(row[0])
        self.assertEqual(stored["mode"], "HINGLISH")
        self.assertNotIn("evil", stored)                       # only known fields are kept

    def test_requires_token(self):
        self.assertEqual(self.call("POST", "/v1/samples", wav())[0], 401)
        self.assertEqual(self.call("POST", "/v1/samples", wav(), {"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.call("GET", "/v1/me")[0], 401)

    def test_rejects_bad_audio(self):
        _iid, auth = self.register()
        self.assertEqual(self.call("POST", "/v1/samples", b"not a wav at all" * 10, auth)[0], 400)
        self.assertEqual(self.call("POST", "/v1/samples", wav(rate=44100), auth)[0], 400)      # wrong format
        self.assertEqual(self.call("POST", "/v1/samples", wav(0.2), auth)[0], 400)             # too short
        self.assertEqual(self.call("POST", "/v1/samples", wav(80.0), auth)[0], 400)            # too long (over the length limit)
        self.assertEqual(self.call("GET", "/v1/me", headers=auth)[1], {"samples": 0})

    def test_delete_removes_everything(self):
        iid, auth = self.register()
        for _ in range(3):
            self.assertEqual(self.call("POST", "/v1/samples", wav(1.5), auth)[0], 200)
        folder = Path(self.tmp.name) / "audio" / iid[:2] / iid
        self.assertEqual(len(list(folder.glob("*.wav"))), 3)
        code, j = self.call("DELETE", "/v1/me", headers=auth)
        self.assertEqual((code, j), (200, {"deleted_samples": 3}))
        self.assertFalse(folder.exists())
        self.assertEqual(self.call("GET", "/v1/me", headers=auth)[0], 401)        # the token is gone too

    def test_daily_limit(self):
        _iid, auth = self.register()
        old = ingest.DAILY_SAMPLES_PER_INSTALL
        ingest.DAILY_SAMPLES_PER_INSTALL = 2
        try:
            codes = [self.call("POST", "/v1/samples", wav(1.0), auth)[0] for _ in range(3)]
        finally:
            ingest.DAILY_SAMPLES_PER_INSTALL = old
        self.assertEqual(codes, [200, 200, 429])


if __name__ == "__main__":
    unittest.main()
