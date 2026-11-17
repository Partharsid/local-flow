"""LocalFlow data-contribution server.

Receives recordings (and what the app typed) that people have OPTED IN to share, so they can be used to
fine-tune LocalFlow's models. Standard library only: no dependencies to install or patch.

Design rules
- Nothing is accepted without a registered install token, and every install must declare the consent text
  version it accepted.
- The server stores no IP addresses, no device identifiers and no user accounts: an install is a random id.
- "Delete my data" (DELETE /v1/me) removes an install's audio, metadata and token, immediately and for good.
- Samples are validated (16 kHz mono 16-bit WAV, bounded length and size) and rate limited per install.

Endpoints
  GET    /healthz
  POST   /v1/register   {"consent_version": "1"}                       -> {"install_id", "token"}
  POST   /v1/samples    Authorization: Bearer <token>, header X-Meta (JSON), body = WAV  -> {"sample_id"}
  GET    /v1/me         Authorization: Bearer <token>                  -> {"samples": N}
  DELETE /v1/me         Authorization: Bearer <token>                  -> {"deleted_samples": N}

Run:  python ingest.py            (env: LF_DATA_DIR, LF_PORT, LF_HOST)
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 4 * 1024 * 1024            # 4 MB: about 2 minutes of 16 kHz mono 16-bit audio
MAX_SECONDS = 70.0
MIN_SECONDS = 0.5
MAX_META = 8 * 1024
DAILY_SAMPLES_PER_INSTALL = 400
REGISTRATIONS_PER_HOUR = 60           # whole server: a crude brake on token farming
ALLOWED_META = {"mode", "model", "model_hinglish", "tier", "app_version", "language", "stt_ms", "audio_sec",
                "raw_text", "final_text", "polished", "android", "device"}


class Store:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        (self.dir / "audio").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.dir / "meta.db"), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS installs(
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, consent_version TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS samples(
                    id TEXT PRIMARY KEY, install_id TEXT NOT NULL, path TEXT NOT NULL, bytes INTEGER, seconds REAL,
                    meta TEXT, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS samples_install ON samples(install_id, created);
            """)
        self._registrations: list[float] = []

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def register(self, consent_version: str) -> tuple[str, str] | None:
        now = time.time()
        with self.lock:
            self._registrations = [t for t in self._registrations if now - t < 3600]
            if len(self._registrations) >= REGISTRATIONS_PER_HOUR:
                return None
            self._registrations.append(now)
            install_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO installs VALUES(?,?,?,?)", (install_id, self._hash(token), consent_version, now))
            self.db.commit()
        return install_id, token

    def install_for(self, token: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT id FROM installs WHERE token_hash=?", (self._hash(token),)).fetchone()
        return row[0] if row else None

    def add_sample(self, install_id: str, wav: bytes, seconds: float, meta: dict) -> str | None:
        now = time.time()
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM samples WHERE install_id=? AND created>?",
                                (install_id, now - 86400)).fetchone()[0]
            if n >= DAILY_SAMPLES_PER_INSTALL:
                return None
        sample_id = uuid.uuid4().hex
        folder = self.dir / "audio" / install_id[:2] / install_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sample_id}.wav"
        path.write_bytes(wav)
        with self.lock:
            self.db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?)",
                            (sample_id, install_id, str(path.relative_to(self.dir)), len(wav), seconds,
                             json.dumps(meta, ensure_ascii=False), now))
            self.db.commit()
        return sample_id

    def count(self, install_id: str) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM samples WHERE install_id=?", (install_id,)).fetchone()[0]

    def delete_install(self, install_id: str) -> int:
        n = self.count(install_id)
        shutil.rmtree(self.dir / "audio" / install_id[:2] / install_id, ignore_errors=True)
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE install_id=?", (install_id,))
            self.db.execute("DELETE FROM installs WHERE id=?", (install_id,))
            self.db.commit()
        return n


def check_wav(data: bytes) -> tuple[float | None, str]:
    """(seconds, '') for an acceptable 16 kHz mono 16-bit PCM WAV, else (None, reason)."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None, "not a WAV file"
    try:
        pos, fmt, data_len = 12, None, None
        while pos + 8 <= len(data):
            cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
            if cid == b"fmt ":
                fmt = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
            elif cid == b"data":
                data_len = min(size, len(data) - pos - 8)
                break
            pos += 8 + size + (size & 1)
        if fmt is None or data_len is None:
            return None, "bad WAV structure"
        tag, channels, rate, _bps, _align, bits = fmt
        if (tag, channels, rate, bits) != (1, 1, 16000, 16):
            return None, "expected 16 kHz mono 16-bit PCM"
        seconds = data_len / 32000.0
    except struct.error:
        return None, "bad WAV header"
    if seconds < MIN_SECONDS:
        return None, "too short"
    if seconds > MAX_SECONDS:
        return None, "too long"
    return seconds, ""


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "lf-ingest"

        def log_message(self, *_a):                # no access log: no IPs, no timing of who sent what
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _token(self) -> str | None:
            h = self.headers.get("Authorization", "")
            return h[7:].strip() if h.startswith("Bearer ") else None

        def _install(self) -> str | None:
            t = self._token()
            return store.install_for(t) if t else None

        def _body(self, limit: int) -> bytes | None:
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if n <= 0 or n > limit:
                return None
            return self.rfile.read(n)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if self.path == "/v1/me":
                iid = self._install()
                return self._send(200, {"samples": store.count(iid)}) if iid else self._send(401, {"error": "unauthorized"})
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path == "/v1/register":
                body = self._body(2048)
                try:
                    cv = str(json.loads(body or b"{}").get("consent_version", ""))
                except ValueError:
                    cv = ""
                if not cv:
                    return self._send(400, {"error": "consent_version is required"})
                reg = store.register(cv)
                if reg is None:
                    return self._send(429, {"error": "try again later"})
                return self._send(200, {"install_id": reg[0], "token": reg[1]})
            if self.path == "/v1/samples":
                iid = self._install()
                if not iid:
                    return self._send(401, {"error": "unauthorized"})
                raw_meta = self.headers.get("X-Meta", "{}")
                if len(raw_meta) > MAX_META:
                    return self._send(400, {"error": "metadata too large"})
                try:
                    meta = json.loads(raw_meta)
                    assert isinstance(meta, dict)
                except (ValueError, AssertionError):
                    return self._send(400, {"error": "bad metadata"})
                meta = {k: v for k, v in meta.items() if k in ALLOWED_META}
                wav = self._body(MAX_BODY)
                if wav is None:
                    return self._send(413, {"error": "bad or oversized body"})
                seconds, why = check_wav(wav)
                if seconds is None:
                    return self._send(400, {"error": why})
                sid = store.add_sample(iid, wav, seconds, meta)
                if sid is None:
                    return self._send(429, {"error": "daily limit reached"})
                return self._send(200, {"sample_id": sid})
            self._send(404, {"error": "not found"})

        def do_DELETE(self):  # noqa: N802
            if self.path == "/v1/me":
                iid = self._install()
                if not iid:
                    return self._send(401, {"error": "unauthorized"})
                return self._send(200, {"deleted_samples": store.delete_install(iid)})
            self._send(404, {"error": "not found"})

    return Handler


def serve(data_dir: Path, host: str, port: int) -> ThreadingHTTPServer:
    store = Store(data_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    httpd.daemon_threads = True
    httpd.store = store                       # so callers (tests) can close the database
    return httpd


if __name__ == "__main__":
    d = Path(os.environ.get("LF_DATA_DIR", "./data"))
    h, p = os.environ.get("LF_HOST", "0.0.0.0"), int(os.environ.get("LF_PORT", "8080"))
    print(f"LocalFlow ingest listening on {h}:{p}, data in {d.resolve()}", flush=True)
    serve(d, h, p).serve_forever()
