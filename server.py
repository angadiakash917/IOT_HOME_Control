#!/usr/bin/env python3
"""Homesense local IoT server. Standard library only."""
from __future__ import annotations

import csv
import hashlib
import hmac
import http.cookies
import http.server
import io
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
DEFAULTS = {
    "host": "127.0.0.1", "port": 8080, "database": "data/homesense.sqlite3",
    "ldr_dark_raw": 0, "ldr_bright_raw": 1023,
    "online_timeout_seconds": 60, "cookie_secure": False,
}


def load_config():
    cfg = DEFAULTS.copy()
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Could not read config.json: {exc}")
    cfg["host"] = os.environ.get("HOMESENSE_HOST", cfg["host"])
    cfg["port"] = int(os.environ.get("PORT", os.environ.get("HOMESENSE_PORT", cfg["port"])))
    cfg["cookie_secure"] = os.environ.get("HOMESENSE_COOKIE_SECURE", str(cfg["cookie_secure"])).lower() in ("1", "true", "yes")
    db_path = os.environ.get("HOMESENSE_DB", cfg["database"])
    cfg["database_path"] = (ROOT / db_path).resolve() if not Path(db_path).is_absolute() else Path(db_path)
    if int(cfg["ldr_bright_raw"]) == int(cfg["ldr_dark_raw"]):
        raise SystemExit("ldr_dark_raw and ldr_bright_raw must be different.")
    if not 0 <= int(cfg["ldr_dark_raw"]) <= 1023 or not 0 <= int(cfg["ldr_bright_raw"]) <= 1023:
        raise SystemExit("LDR calibration endpoints must be between 0 and 1023.")
    if not 1 <= cfg["port"] <= 65535:
        raise SystemExit("port must be between 1 and 65535.")
    if int(cfg["online_timeout_seconds"]) < 10:
        raise SystemExit("online_timeout_seconds must be at least 10.")
    return cfg


CFG = load_config()
CFG["database_path"].parent.mkdir(parents=True, exist_ok=True)


def now_dt():
    return datetime.now(timezone.utc)


def now_iso():
    return now_dt().isoformat(timespec="seconds")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        method, rounds, salt_hex, expected = stored.split("$")
        if method != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@contextmanager
def db():
    conn = sqlite3.connect(CFG["database_path"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_db():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, theme TEXT NOT NULL DEFAULT 'light',
          energy_rate REAL NOT NULL DEFAULT 0,
          notify_temperature INTEGER NOT NULL DEFAULT 1, notify_light INTEGER NOT NULL DEFAULT 1,
          notify_device INTEGER NOT NULL DEFAULT 1, notify_appliance INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          csrf_token TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
        CREATE TABLE IF NOT EXISTS password_resets (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS devices (
          id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, hardware_id TEXT UNIQUE, firmware_version TEXT,
          wifi_rssi INTEGER, ip_address TEXT, relay_on INTEGER, rated_watts REAL NOT NULL DEFAULT 60,
          current_power_w REAL, last_seen TEXT, online_state INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS devices_owner_idx ON devices(owner_id);
        CREATE TABLE IF NOT EXISTS readings (
          id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          temperature REAL, humidity REAL, ldr_raw INTEGER, light_pct REAL, light_level TEXT, power_w REAL,
          relay_on INTEGER, wifi_rssi INTEGER, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS readings_device_time_idx ON readings(device_id, created_at);
        CREATE TABLE IF NOT EXISTS commands (
          id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          desired_state INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL, delivered_at TEXT, completed_at TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS commands_device_status_idx ON commands(device_id, status);
        CREATE TABLE IF NOT EXISTS appliance_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          state INTEGER NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS appliance_events_user_time_idx ON appliance_events(user_id, created_at);
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device_id TEXT REFERENCES devices(id) ON DELETE CASCADE, kind TEXT NOT NULL,
          title TEXT NOT NULL, message TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notifications_user_time_idx ON notifications(user_id, created_at);
        CREATE TABLE IF NOT EXISTS alert_states (
          device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE, kind TEXT NOT NULL,
          active INTEGER NOT NULL, PRIMARY KEY(device_id, kind)
        );
        """)
        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "energy_rate" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN energy_rate REAL NOT NULL DEFAULT 0")
        device_columns = {row[1] for row in conn.execute("PRAGMA table_info(devices)")}
        if "rated_watts" not in device_columns:
            conn.execute("ALTER TABLE devices ADD COLUMN rated_watts REAL NOT NULL DEFAULT 60")
        if "current_power_w" not in device_columns:
            conn.execute("ALTER TABLE devices ADD COLUMN current_power_w REAL")
        if "hardware_id" not in device_columns:
            conn.execute("ALTER TABLE devices ADD COLUMN hardware_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS devices_hardware_id_idx ON devices(hardware_id) WHERE hardware_id IS NOT NULL")
        reading_columns = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
        if "power_w" not in reading_columns:
            conn.execute("ALTER TABLE readings ADD COLUMN power_w REAL")


initialize_db()


def notify(conn, user_id, device_id, kind, title, message):
    pref = {"temperature": "notify_temperature", "sensor": "notify_temperature", "light": "notify_light", "device": "notify_device", "appliance": "notify_appliance"}.get(kind)
    if pref:
        row = conn.execute(f"SELECT {pref} AS enabled FROM users WHERE id=?", (user_id,)).fetchone()
        if row and not row["enabled"]:
            return
    conn.execute("INSERT INTO notifications(user_id,device_id,kind,title,message,created_at) VALUES(?,?,?,?,?,?)",
                 (user_id, device_id, kind, title, message, now_iso()))


def alert_transition(conn, device, kind, active, title, message):
    row = conn.execute("SELECT active FROM alert_states WHERE device_id=? AND kind=?", (device["id"], kind)).fetchone()
    previous = bool(row["active"]) if row else False
    conn.execute("INSERT INTO alert_states(device_id,kind,active) VALUES(?,?,?) ON CONFLICT(device_id,kind) DO UPDATE SET active=excluded.active",
                 (device["id"], kind, int(active)))
    if active and not previous:
        notify(conn, device["owner_id"], device["id"], kind, title, message)


def update_presence(conn, user_id=None):
    params = []
    sql = "SELECT * FROM devices"
    if user_id is not None:
        sql += " WHERE owner_id=?"
        params.append(user_id)
    for device in conn.execute(sql, params).fetchall():
        online = False
        if device["last_seen"]:
            try:
                seen = datetime.fromisoformat(device["last_seen"])
                online = (now_dt() - seen).total_seconds() <= int(CFG["online_timeout_seconds"])
            except ValueError:
                pass
        old = bool(device["online_state"])
        if old and not online:
            notify(conn, device["owner_id"], device["id"], "device", "Device disconnected", f"{device['name']} has not checked in recently.")
        if online:
            conn.execute("UPDATE devices SET online_state=1 WHERE id=?", (device["id"],))
        else:
            # A stale USB session must not leave the dashboard showing a bulb
            # as on after its browser serial connection has disappeared.
            conn.execute("UPDATE devices SET online_state=0,relay_on=0,current_power_w=NULL WHERE id=?", (device["id"],))


def owned_device(conn, user_id, device_id):
    return conn.execute("SELECT * FROM devices WHERE id=? AND owner_id=?", (device_id, user_id)).fetchone()


def device_json(device):
    online = bool(device["online_state"])
    return {
        "id": device["id"], "name": device["name"], "status": "online" if online else ("waiting" if not device["last_seen"] else "offline"),
        "wifiRssi": device["wifi_rssi"], "relayOn": None if device["relay_on"] is None else bool(device["relay_on"]),
        "lastSeen": device["last_seen"], "firmwareVersion": device["firmware_version"],
        "ipAddress": device["ip_address"], "createdAt": device["created_at"],
        "ratedWatts": device["rated_watts"], "currentPowerW": device["current_power_w"],
        "serverPort": CFG["port"],
    }


def latest_reading(conn, device_id):
    row = conn.execute("SELECT * FROM readings WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
    return reading_json(row) if row else None


def reading_json(row):
    return {"id": row["id"], "temperature": row["temperature"], "humidity": row["humidity"],
            "ldrRaw": row["ldr_raw"], "lightPct": row["light_pct"], "lightLevel": row["light_level"],
            "relayOn": None if row["relay_on"] is None else bool(row["relay_on"]), "wifiRssi": row["wifi_rssi"],
            "powerW": row["power_w"], "createdAt": row["created_at"]}


def clean_float(value, field, minimum, maximum, nullable=True):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number" if value is not None else f"{field} is required")
    value = float(value)
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"{field} is out of range")
    return value


def selected_range(query):
    key = query.get("range", ["week"])[0]
    end = now_dt()
    if key == "day":
        start = end - timedelta(days=1)
    elif key == "year":
        start = end - timedelta(days=365)
    elif key == "month":
        start = end - timedelta(days=30)
    elif key == "custom":
        try:
            start = datetime.fromisoformat(query.get("from", [""])[0]).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(query.get("to", [""])[0]).replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise ValueError("Choose a valid start and end date")
        if end <= start or (end - start).days > 366:
            raise ValueError("Date range must be valid and no longer than one year")
    else:
        key = "week"
        start = end - timedelta(days=7)
    return key, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def energy_range(key):
    end = now_dt()
    days, buckets = {"day": (1, 24), "week": (7, 7), "month": (30, 30), "year": (365, 12)}.get(key, (7, 7))
    return key if key in ("day", "week", "month", "year") else "week", end - timedelta(days=days), end, buckets


def add_energy_interval(buckets, begin, finish, watts, start, end):
    begin = max(begin, start)
    finish = min(finish, end)
    total_seconds = (end - start).total_seconds()
    if finish <= begin or watts <= 0 or total_seconds <= 0:
        return
    count = len(buckets)
    cursor = begin
    while cursor < finish:
        index = min(count - 1, int((cursor - start).total_seconds() * count / total_seconds))
        bucket_end = start + timedelta(seconds=total_seconds * (index + 1) / count)
        stop = min(finish, bucket_end)
        buckets[index] += watts * (stop - cursor).total_seconds() / 3_600_000
        cursor = stop


def appliance_energy(conn, device, start, end, bucket_count):
    buckets = [0.0] * bucket_count
    rated_watts = float(device["rated_watts"] or 0)
    samples = conn.execute(
        "SELECT created_at,power_w FROM readings WHERE device_id=? AND power_w IS NOT NULL AND created_at>=? AND created_at<? ORDER BY created_at",
        (device["id"], start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
    ).fetchall()
    metered = len(samples) >= 2
    if metered:
        parsed = [(datetime.fromisoformat(row["created_at"]), float(row["power_w"])) for row in samples]
        for (time_a, watts_a), (time_b, watts_b) in zip(parsed, parsed[1:]):
            gap = (time_b - time_a).total_seconds()
            if 0 < gap <= 300:
                add_energy_interval(buckets, time_a, time_b, (watts_a + watts_b) / 2, start, end)
    else:
        events = conn.execute(
            "SELECT state,created_at FROM appliance_events WHERE device_id=? AND created_at<? ORDER BY created_at,id",
            (device["id"], end.isoformat(timespec="seconds")),
        ).fetchall()
        state = False
        cursor = start
        has_event = False
        for event in events:
            stamp = datetime.fromisoformat(event["created_at"])
            if stamp < start:
                state = bool(event["state"])
                has_event = True
                continue
            if state:
                add_energy_interval(buckets, cursor, stamp, rated_watts, start, end)
            state = bool(event["state"])
            cursor = stamp
            has_event = True
        if state:
            estimate_end = end
            if device["last_seen"]:
                estimate_end = min(estimate_end, datetime.fromisoformat(device["last_seen"]))
            if has_event:
                add_energy_interval(buckets, cursor, estimate_end, rated_watts, start, end)

    energy_kwh = sum(buckets)
    online = bool(device["online_state"])
    if online and device["current_power_w"] is not None:
        current_watts = max(0.0, float(device["current_power_w"]))
        current_source = "metered"
    elif online and bool(device["relay_on"]):
        current_watts = rated_watts
        current_source = "estimated"
    else:
        current_watts = 0.0
        current_source = "estimated"
    return {
        "id": device["id"], "name": device["name"], "status": "online" if online else "offline",
        "relayOn": None if device["relay_on"] is None else bool(device["relay_on"]),
        "ratedWatts": rated_watts, "currentWatts": round(current_watts, 2),
        "energyKwh": round(energy_kwh, 4), "calculation": "metered" if metered else "estimated",
        "buckets": [round(value, 4) for value in buckets],
    }


def energy_report(conn, user_id, range_key):
    range_key, start, end, bucket_count = energy_range(range_key)
    user = conn.execute("SELECT energy_rate FROM users WHERE id=?", (user_id,)).fetchone()
    rate = float(user["energy_rate"] or 0) if user else 0.0
    devices = conn.execute("SELECT * FROM devices WHERE owner_id=? ORDER BY created_at", (user_id,)).fetchall()
    rows = [appliance_energy(conn, device, start, end, bucket_count) for device in devices]
    total_buckets = [round(sum(item["buckets"][index] for item in rows), 4) for index in range(bucket_count)]
    total_kwh = sum(item["energyKwh"] for item in rows)
    total_watts = sum(item["currentWatts"] for item in rows)
    labels = []
    width = (end - start) / bucket_count
    for index in range(bucket_count):
        point = start + width * index + width / 2
        labels.append(point.strftime("%H:%M" if range_key == "day" else "%b %d" if range_key != "year" else "%b"))
    for item in rows:
        item["cost"] = round(item["energyKwh"] * rate, 2)
        item.pop("buckets")
    return {
        "range": range_key, "ratePerKwh": rate,
        "currentPowerW": round(total_watts, 2), "energyKwh": round(total_kwh, 4),
        "cost": round(total_kwh * rate, 2), "calculation": "metered" if rows and all(row["calculation"] == "metered" for row in rows) else "estimated",
        "appliancesOn": sum(1 for row in rows if row["relayOn"] is True and row["status"] == "online"),
        "appliances": rows, "timeline": {"labels": labels, "kwh": total_buckets},
        "from": start.isoformat(timespec="seconds"), "to": end.isoformat(timespec="seconds"),
    }


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "HomeSense/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stdout.write(f"[{now_iso()}] {self.address_string()} {fmt % args}\n")

    def _headers(self, status=200, content_type="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://*.firebaseio.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        for key, value in (extra or {}).items():
            self.send_header(key, value)

    def send_json(self, data, status=200, extra=None):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, extra=extra)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status)

    def send_bytes(self, data, content_type, status=200, extra=None):
        self._headers(status, content_type, extra)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid content length")
        if length <= 0 or length > 262144:
            raise ValueError("Request body is empty or too large")
        raw = self.rfile.read(length)
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must be valid JSON")
        if not isinstance(result, dict):
            raise ValueError("Request body must be a JSON object")
        return result

    def current_user(self, conn):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None, None
        morsel = cookie.get("hs_session")
        if not morsel:
            return None, None
        row = conn.execute("SELECT s.csrf_token,s.expires_at,u.id,u.name,u.email,u.theme,u.notify_temperature,u.notify_light,u.notify_device,u.notify_appliance FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (token_hash(morsel.value),)).fetchone()
        if not row:
            return None, None
        if row["expires_at"] < now_iso():
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(morsel.value),))
            return None, None
        return row, morsel.value

    def require_user(self, conn):
        user, secret = self.current_user(conn)
        if not user:
            self.send_error_json(401, "Sign in to continue")
            return None, None
        if self.command in ("POST", "PUT", "PATCH", "DELETE"):
            csrf = self.headers.get("X-CSRF-Token", "")
            if not csrf or not hmac.compare_digest(csrf, user["csrf_token"]):
                self.send_error_json(403, "Your session token expired. Refresh the page and try again.")
                return None, None
        return user, secret

    def login_cookie(self, user_id):
        secret = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expires = (now_dt() + timedelta(days=7)).isoformat(timespec="seconds")
        with db() as conn:
            conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)", (token_hash(secret), user_id, csrf, expires, now_iso()))
        cookie = f"hs_session={secret}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800"
        if CFG.get("cookie_secure"):
            cookie += "; Secure"
        return csrf, {"Set-Cookie": cookie}

    def device_auth(self, conn):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        supplied = token_hash(auth[7:].strip())
        row = conn.execute("SELECT * FROM devices WHERE token_hash=?", (supplied,)).fetchone()
        return row

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path.startswith("/api/"):
            try:
                self.api_get(path, parse_qs(parsed.query))
            except (ValueError, sqlite3.Error) as exc:
                self.send_error_json(400, str(exc) if isinstance(exc, ValueError) else "The database could not complete this request")
            except Exception as exc:
                print(f"API GET error: {exc}", file=sys.stderr)
                self.send_error_json(500, "The server could not complete this request")
            return
        self.serve_static(path)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            payload = self.body()
            self.api_post(path, payload)
        except (ValueError, sqlite3.Error) as exc:
            self.send_error_json(400, str(exc) if isinstance(exc, ValueError) else "The database could not complete this request")
        except Exception as exc:
            print(f"API POST error: {exc}", file=sys.stderr)
            self.send_error_json(500, "The server could not complete this request")

    def do_PUT(self):
        self._mutate()

    def do_PATCH(self):
        self._mutate()

    def do_DELETE(self):
        self._mutate()

    def _mutate(self):
        path = unquote(urlparse(self.path).path)
        try:
            payload = self.body() if self.command != "DELETE" else {}
            self.api_mutate(path, payload)
        except (ValueError, sqlite3.Error) as exc:
            self.send_error_json(400, str(exc) if isinstance(exc, ValueError) else "The database could not complete this request")
        except Exception as exc:
            print(f"API {self.command} error: {exc}", file=sys.stderr)
            self.send_error_json(500, "The server could not complete this request")

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        if path in ("/privacy", "/terms"):
            path = "/index.html"
        if path == "/README.md":
            candidate = (ROOT / "README.md").resolve()
            allowed_root = ROOT.resolve()
            content_type = "text/markdown; charset=utf-8"
        else:
            candidate = (WEB / path.lstrip("/")).resolve()
            allowed_root = WEB.resolve()
            content_type = None
        if allowed_root not in candidate.parents and candidate != allowed_root:
            return self.send_error_json(404, "Not found")
        if not candidate.is_file():
            return self.send_error_json(404, "Not found")
        ext = candidate.suffix.lower()
        content_type = content_type or {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
        self.send_bytes(candidate.read_bytes(), content_type)

    def api_get(self, path, query):
        with db() as conn:
            if path == "/api/auth/me":
                user, _ = self.current_user(conn)
                if not user:
                    return self.send_json({"authenticated": False})
                return self.send_json({"authenticated": True, "csrfToken": user["csrf_token"], "user": {"id": user["id"], "name": user["name"], "email": user["email"], "theme": user["theme"]}})
            if path == "/api/setup":
                user, _ = self.require_user(conn)
                if not user:
                    return
                firmware_path = ROOT / "firmware" / "homesense_esp8266" / "homesense_esp8266.ino"
                return self.send_json({"firmware": firmware_path.read_text(encoding="utf-8")})
            if path == "/api/device/command":
                device = self.device_auth(conn)
                if not device:
                    return self.send_error_json(401, "Invalid device token")
                stamp = now_iso()
                expire_before = (now_dt() - timedelta(minutes=2)).isoformat(timespec="seconds")
                retry_before = (now_dt() - timedelta(seconds=15)).isoformat(timespec="seconds")
                conn.execute("UPDATE commands SET status='failed',completed_at=?,error='Device did not acknowledge within two minutes' WHERE device_id=? AND status IN ('pending','delivered') AND created_at<?", (stamp, device["id"], expire_before))
                conn.execute("UPDATE commands SET status='pending',delivered_at=NULL WHERE device_id=? AND status='delivered' AND delivered_at<?", (device["id"], retry_before))
                cmd = conn.execute("SELECT id,desired_state FROM commands WHERE device_id=? AND status='pending' ORDER BY created_at LIMIT 1", (device["id"],)).fetchone()
                if not cmd:
                    return self.send_json({"command": None})
                conn.execute("UPDATE commands SET status='delivered',delivered_at=? WHERE id=?", (now_iso(), cmd["id"]))
                return self.send_json({"command": {"id": cmd["id"], "state": bool(cmd["desired_state"])}})

            command_match = re.fullmatch(r"/api/devices/([^/]+)/commands/([a-f0-9]+)", path)
            if command_match:
                user, _ = self.require_user(conn)
                if not user:
                    return
                device = owned_device(conn, user["id"], command_match.group(1))
                if not device:
                    return self.send_error_json(404, "Device not found")
                command = conn.execute("SELECT id,status,error,desired_state FROM commands WHERE id=? AND device_id=? AND user_id=?", (command_match.group(2), device["id"], user["id"])).fetchone()
                if not command:
                    return self.send_error_json(404, "Command not found")
                return self.send_json({"id": command["id"], "status": command["status"], "error": command["error"], "state": bool(command["desired_state"])})

            user, _ = self.require_user(conn)
            if not user:
                return
            user_id = user["id"]
            update_presence(conn, user_id)
            if path == "/api/profile":
                row = conn.execute("SELECT name,email,theme,notify_temperature,notify_light,notify_device,notify_appliance,created_at FROM users WHERE id=?", (user_id,)).fetchone()
                return self.send_json({"profile": dict(row)})
            if path == "/api/devices":
                devices = conn.execute("SELECT * FROM devices WHERE owner_id=? ORDER BY created_at", (user_id,)).fetchall()
                result = []
                for device in devices:
                    item = device_json(device)
                    item["latest"] = latest_reading(conn, device["id"])
                    result.append(item)
                return self.send_json({"devices": result, "serverPort": CFG["port"]})
            if path == "/api/dashboard":
                devices = conn.execute("SELECT * FROM devices WHERE owner_id=? ORDER BY created_at", (user_id,)).fetchall()
                all_devices = []
                for device in devices:
                    item = device_json(device)
                    item["latest"] = latest_reading(conn, device["id"])
                    all_devices.append(item)
                events = conn.execute("SELECT e.id,e.state,e.source,e.created_at,d.name AS device_name FROM appliance_events e JOIN devices d ON d.id=e.device_id WHERE e.user_id=? ORDER BY e.id DESC LIMIT 8", (user_id,)).fetchall()
                unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user_id,)).fetchone()[0]
                return self.send_json({"devices": all_devices, "events": [dict(x) for x in events], "unreadNotifications": unread})
            if path == "/api/energy":
                update_presence(conn, user_id)
                return self.send_json(energy_report(conn, user_id, query.get("range", ["day"])[0]))
            if path == "/api/readings" or path == "/api/readings.csv":
                key, start, end = selected_range(query)
                dev_id = query.get("deviceId", [""])[0]
                if dev_id:
                    device = owned_device(conn, user_id, dev_id)
                    if not device:
                        return self.send_error_json(404, "Device not found")
                else:
                    device = conn.execute("SELECT * FROM devices WHERE owner_id=? ORDER BY created_at LIMIT 1", (user_id,)).fetchone()
                if not device:
                    records = []
                    device_name = None
                else:
                    records = conn.execute("SELECT * FROM readings WHERE device_id=? AND created_at>=? AND created_at<? ORDER BY id", (device["id"], start, end)).fetchall()
                    device_name = device["name"]
                values = [reading_json(x) for x in records]
                if path.endswith(".csv"):
                    out = io.StringIO(newline="")
                    writer = csv.writer(out)
                    writer.writerow(["timestamp_utc", "temperature_c", "humidity_percent", "ldr_raw", "light_percent", "light_level", "relay_on", "wifi_rssi_dbm"])
                    for row in values:
                        writer.writerow([row["createdAt"], row["temperature"], row["humidity"], row["ldrRaw"], row["lightPct"], row["lightLevel"], row["relayOn"], row["wifiRssi"]])
                    return self.send_bytes(out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", extra={"Content-Disposition": f"attachment; filename=homesense-{key}.csv"})
                summary = {}
                for keyname in ("temperature", "humidity", "lightPct"):
                    nums = [row[keyname] for row in values if row[keyname] is not None]
                    summary[keyname] = {"min": round(min(nums), 2) if nums else None, "avg": round(sum(nums) / len(nums), 2) if nums else None, "max": round(max(nums), 2) if nums else None}
                event_rows = []
                if device:
                    event_rows = conn.execute("SELECT state,source,created_at FROM appliance_events WHERE device_id=? AND created_at>=? AND created_at<? ORDER BY id", (device["id"], start, end)).fetchall()
                return self.send_json({"range": key, "from": start, "to": end, "deviceName": device_name, "readings": values, "summary": summary, "applianceEvents": [dict(x) for x in event_rows]})
            if path == "/api/notifications":
                rows = conn.execute("SELECT n.*,d.name AS device_name FROM notifications n LEFT JOIN devices d ON d.id=n.device_id WHERE n.user_id=? ORDER BY n.id DESC LIMIT 250", (user_id,)).fetchall()
                return self.send_json({"notifications": [{"id": r["id"], "kind": r["kind"], "title": r["title"], "message": r["message"], "read": bool(r["is_read"]), "createdAt": r["created_at"], "deviceName": r["device_name"]} for r in rows]})
            return self.send_error_json(404, "API endpoint not found")

    def api_post(self, path, payload):
        if path in ("/api/auth/logout", "/api/profile/password"):
            return self.api_mutate(path, payload)

        if path in ("/api/auth/register", "/api/auth/login"):
            ip = self.client_address[0]
            if not allow_auth_attempt(ip):
                return self.send_error_json(429, "Too many sign-in attempts. Wait a few minutes and try again.")
            email = str(payload.get("email", "")).strip().lower()
            password = str(payload.get("password", ""))
            if path.endswith("register"):
                name = str(payload.get("name", "")).strip()
                if not name or len(name) > 80:
                    return self.send_error_json(400, "Enter a name of up to 80 characters")
                if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
                    return self.send_error_json(400, "Enter a valid email address")
                if len(password) < 10 or len(password) > 200:
                    return self.send_error_json(400, "Use a password between 10 and 200 characters")
                try:
                    with db() as conn:
                        cur = conn.execute("INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)", (name, email, password_hash(password), now_iso()))
                        user_id = cur.lastrowid
                except sqlite3.IntegrityError:
                    return self.send_error_json(409, "An account with that email already exists")
            else:
                with db() as conn:
                    row = conn.execute("SELECT id,password_hash FROM users WHERE email=?", (email,)).fetchone()
                if not row or not check_password(password, row["password_hash"]):
                    return self.send_error_json(401, "Email or password is incorrect")
                user_id = row["id"]
            reset_auth_attempts(ip)
            csrf, headers = self.login_cookie(user_id)
            with db() as conn:
                user = conn.execute("SELECT id,name,email,theme FROM users WHERE id=?", (user_id,)).fetchone()
            return self.send_json({"authenticated": True, "csrfToken": csrf, "user": dict(user)}, 201 if path.endswith("register") else 200, headers)

        if path == "/api/auth/forgot":
            email = str(payload.get("email", "")).strip().lower()
            generic = {"message": "If an account exists for that email, a reset link has been generated."}
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                return self.send_json(generic)
            with db() as conn:
                user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
                if user:
                    raw = secrets.token_urlsafe(32)
                    conn.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
                    conn.execute("INSERT INTO password_resets(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash(raw), user["id"], (now_dt()+timedelta(minutes=30)).isoformat(timespec="seconds"), now_iso()))
                    reset_url = f"http://127.0.0.1:{CFG['port']}/#/reset?token={raw}"
                    print(f"Password reset (local development only) for {email}: {reset_url}")
            return self.send_json(generic)

        if path == "/api/auth/reset":
            raw = str(payload.get("token", ""))
            password = str(payload.get("password", ""))
            if len(password) < 10 or len(password) > 200:
                return self.send_error_json(400, "Use a password between 10 and 200 characters")
            with db() as conn:
                reset = conn.execute("SELECT user_id,expires_at FROM password_resets WHERE token_hash=?", (token_hash(raw),)).fetchone()
                if not reset or reset["expires_at"] < now_iso():
                    return self.send_error_json(400, "This reset link is invalid or has expired")
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(password), reset["user_id"]))
                conn.execute("DELETE FROM password_resets WHERE user_id=?", (reset["user_id"],))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (reset["user_id"],))
            return self.send_json({"message": "Password updated. You can now sign in."})

        if path == "/api/device/pair":
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                token = secrets.token_urlsafe(32)
                existing_names = [row[0] for row in conn.execute("SELECT name FROM devices WHERE owner_id=?", (user["id"],)).fetchall()]
                # Reuse the first bulb if setup is retried before its NodeMCU
                # has reported telemetry. This avoids duplicate offline bulbs.
                pending_bulb = conn.execute(
                    "SELECT id FROM devices WHERE owner_id=? AND name='Bulb' AND last_seen IS NULL ORDER BY created_at,id LIMIT 1",
                    (user["id"],),
                ).fetchone()
                if pending_bulb:
                    conn.execute(
                        "UPDATE devices SET token_hash=?,hardware_id=NULL,created_at=? WHERE id=?",
                        (token_hash(token), now_iso(), pending_bulb["id"]),
                    )
                    return self.send_json({"deviceId": pending_bulb["id"], "deviceToken": token})

                numbers = [int(match.group(1) or 1) for existing_name in existing_names if (match := re.fullmatch(r"Bulb(?: (\d+))?", existing_name))]
                device_number = max(numbers, default=0) + 1
                device_id = "esp-" + uuid.uuid4().hex[:12]
                name = "Bulb" if device_number == 1 else f"Bulb {device_number}"
                conn.execute("INSERT INTO devices(id,owner_id,name,rated_watts,token_hash,created_at) VALUES(?,?,?,?,?,?)", (device_id, user["id"], name, 60, token_hash(token), now_iso()))
                return self.send_json({"deviceId": device_id, "deviceToken": token}, 201)

        if path == "/api/energy/settings":
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                rate = clean_float(payload.get("ratePerKwh"), "ratePerKwh", 0, 100000, nullable=False)
                conn.execute("UPDATE users SET energy_rate=? WHERE id=?", (rate, user["id"]))
                return self.send_json({"saved": True, "ratePerKwh": rate})

        if path == "/api/local/disconnect":
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                device = conn.execute(
                    "SELECT * FROM devices WHERE owner_id=? AND name='Bulb' ORDER BY created_at,id LIMIT 1",
                    (user["id"],),
                ).fetchone()
                if not device:
                    return self.send_json({"accepted": True, "device": False})
                timestamp = now_iso()
                if device["relay_on"]:
                    conn.execute(
                        "INSERT INTO appliance_events(device_id,user_id,state,source,created_at) VALUES(?,?,?,?,?)",
                        (device["id"], user["id"], 0, "USB", timestamp),
                    )
                if device["online_state"]:
                    notify(conn, user["id"], device["id"], "device", "NodeMCU disconnected", "The USB serial connection to HomeSense has ended.")
                conn.execute(
                    "UPDATE devices SET online_state=0,relay_on=0,current_power_w=NULL WHERE id=?",
                    (device["id"],),
                )
                return self.send_json({"accepted": True, "device": True})

        if path == "/api/local/telemetry":
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                try:
                    temp = clean_float(payload.get("temperature"), "temperature", -40, 80)
                    humidity = clean_float(payload.get("humidity"), "humidity", 0, 100)
                    raw = payload.get("ldrRaw")
                    if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 1023):
                        raise ValueError("ldrRaw must be an integer between 0 and 1023")
                    relay = payload.get("relayOn")
                    if not isinstance(relay, bool):
                        raise ValueError("relayOn must be true or false")
                except ValueError as exc:
                    return self.send_error_json(400, str(exc))

                device = conn.execute(
                    "SELECT * FROM devices WHERE owner_id=? AND name='Bulb' ORDER BY created_at,id LIMIT 1",
                    (user["id"],),
                ).fetchone()
                if not device:
                    device_id = "hs-" + uuid.uuid4().hex[:12]
                    conn.execute(
                        "INSERT INTO devices(id,owner_id,name,rated_watts,token_hash,created_at) VALUES(?,?,?,?,?,?)",
                        (device_id, user["id"], "Bulb", 60, token_hash(secrets.token_urlsafe(32)), now_iso()),
                    )
                    device = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()

                pct = None
                level = None
                if raw is not None:
                    dark, bright = int(CFG["ldr_dark_raw"]), int(CFG["ldr_bright_raw"])
                    pct = round(max(0, min(100, (raw - dark) * 100 / (bright - dark))), 2)
                    level = "Low" if pct < 33 else ("Medium" if pct < 67 else "High")
                previous_seen = device["last_seen"]
                was_online = bool(device["online_state"])
                timestamp = now_iso()
                conn.execute(
                    "INSERT INTO readings(device_id,temperature,humidity,ldr_raw,light_pct,light_level,power_w,relay_on,wifi_rssi,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (device["id"], temp, humidity, raw, pct, level, None, int(relay), None, timestamp),
                )
                conn.execute(
                    "UPDATE devices SET last_seen=?,online_state=1,relay_on=?,current_power_w=NULL,wifi_rssi=NULL,firmware_version='2.0.0-usb',ip_address='USB serial' WHERE id=?",
                    (timestamp, int(relay), device["id"]),
                )
                if not previous_seen:
                    notify(conn, device["owner_id"], device["id"], "device", "NodeMCU connected", "HomeSense is receiving sensor readings over USB.")
                elif not was_online:
                    notify(conn, device["owner_id"], device["id"], "device", "NodeMCU reconnected", "USB sensor readings are arriving again.")
                if temp is not None:
                    alert_transition(conn, device, "temperature", temp >= 35, "High temperature", f"Temperature reached {temp:.1f} °C (alert threshold: 35 °C).")
                    alert_transition(conn, device, "sensor", False, "", "")
                else:
                    alert_transition(conn, device, "sensor", True, "Temperature sensor unavailable", "The DHT11 did not report a valid reading.")
                if pct is not None:
                    alert_transition(conn, device, "light", pct <= 15 or pct >= 90, "Light level alert", f"Ambient light is {pct:.0f}% ({level.lower()}).")
                old_relay = device["relay_on"]
                if old_relay is None or bool(old_relay) != relay:
                    conn.execute(
                        "INSERT INTO appliance_events(device_id,user_id,state,source,created_at) VALUES(?,?,?,?,?)",
                        (device["id"], device["owner_id"], int(relay), "USB", timestamp),
                    )
                return self.send_json({"accepted": True, "receivedAt": timestamp, "lightPct": pct, "lightLevel": level})

        if path == "/api/device/telemetry":
            with db() as conn:
                device = self.device_auth(conn)
                if not device:
                    return self.send_error_json(401, "Invalid device token")
                try:
                    temp = clean_float(payload.get("temperature"), "temperature", -40, 80)
                    humidity = clean_float(payload.get("humidity"), "humidity", 0, 100)
                    power_w = clean_float(payload.get("powerW"), "powerW", 0, 100000)
                    raw = payload.get("ldrRaw")
                    if raw is not None:
                        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > 1023:
                            raise ValueError("ldrRaw must be an integer between 0 and 1023")
                    relay = payload.get("relayOn")
                    if not isinstance(relay, bool):
                        raise ValueError("relayOn must be true or false")
                    rssi = payload.get("wifiRssi")
                    if rssi is not None and (isinstance(rssi, bool) or not isinstance(rssi, int) or not -127 <= rssi <= 0):
                        raise ValueError("wifiRssi must be an integer from -127 to 0")
                    fw = str(payload.get("firmwareVersion", ""))[:40]
                    pct = None
                    level = None
                    if raw is not None:
                        dark, bright = int(CFG["ldr_dark_raw"]), int(CFG["ldr_bright_raw"])
                        pct = round(max(0, min(100, (raw - dark) * 100 / (bright - dark))), 2)
                        level = "Low" if pct < 33 else ("Medium" if pct < 67 else "High")
                except ValueError as exc:
                    return self.send_error_json(400, str(exc))
                previous_seen = device["last_seen"]
                was_online = bool(device["online_state"])
                timestamp = now_iso()
                conn.execute("INSERT INTO readings(device_id,temperature,humidity,ldr_raw,light_pct,light_level,power_w,relay_on,wifi_rssi,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (device["id"], temp, humidity, raw, pct, level, power_w, int(relay), rssi, timestamp))
                conn.execute("UPDATE devices SET last_seen=?,online_state=1,relay_on=?,current_power_w=?,wifi_rssi=?,firmware_version=?,ip_address=? WHERE id=?",
                             (timestamp, int(relay), power_w, rssi, fw, self.client_address[0], device["id"]))
                if not previous_seen:
                    notify(conn, device["owner_id"], device["id"], "device", "Device connected", f"{device['name']} sent its first reading.")
                elif not was_online:
                    notify(conn, device["owner_id"], device["id"], "device", "Device reconnected", f"{device['name']} is sending readings again.")
                if temp is not None:
                    alert_transition(conn, device, "temperature", temp >= 35, "High temperature", f"Temperature reached {temp:.1f} °C (alert threshold: 35 °C).")
                else:
                    alert_transition(conn, device, "sensor", True, "Temperature sensor unavailable", f"{device['name']} did not report a valid DHT11 reading.")
                if temp is not None:
                    alert_transition(conn, device, "sensor", False, "", "")
                if pct is not None:
                    alert_transition(conn, device, "light", pct <= 15 or pct >= 90, "Light level alert", f"Ambient light is {pct:.0f}% ({level.lower()}).")
                if device["relay_on"] is None or bool(device["relay_on"]) != relay:
                    conn.execute("INSERT INTO appliance_events(device_id,user_id,state,source,created_at) VALUES(?,?,?,?,?)", (device["id"], device["owner_id"], int(relay), "physical", timestamp))
                return self.send_json({"accepted": True, "receivedAt": timestamp, "lightPct": pct, "lightLevel": level})

        if path == "/api/device/command/result":
            with db() as conn:
                device = self.device_auth(conn)
                if not device:
                    return self.send_error_json(401, "Invalid device token")
                command_id = str(payload.get("commandId", ""))
                success = payload.get("success") is True
                state = payload.get("relayOn")
                if not isinstance(state, bool):
                    return self.send_error_json(400, "relayOn must be true or false")
                cmd = conn.execute("SELECT * FROM commands WHERE id=? AND device_id=?", (command_id, device["id"])).fetchone()
                if not cmd or cmd["status"] not in ("delivered", "pending"):
                    return self.send_error_json(404, "Command not found or already completed")
                err = None
                if success and bool(state) != bool(cmd["desired_state"]):
                    success = False
                    err = "NodeMCU-reported relay state did not match the requested state"
                timestamp = now_iso()
                if not success and err is None:
                    err = str(payload.get("error", ""))[:200] or "NodeMCU could not apply the relay command"
                conn.execute("UPDATE commands SET status=?,completed_at=?,error=? WHERE id=?", ("complete" if success else "failed", timestamp, err, command_id))
                old_state = device["relay_on"]
                conn.execute("UPDATE devices SET relay_on=?,last_seen=?,online_state=1 WHERE id=?", (int(state), timestamp, device["id"]))
                if success and (old_state is None or bool(old_state) != state):
                    conn.execute("INSERT INTO appliance_events(device_id,user_id,state,source,created_at) VALUES(?,?,?,?,?)", (device["id"], device["owner_id"], int(state), "web", timestamp))
                    notify(conn, device["owner_id"], device["id"], "appliance", "Bulb switched on" if state else "Bulb switched off", f"{device['name']} reports the bulb {'on' if state else 'off'}.")
                return self.send_json({"accepted": True})

        if path == "/api/devices":
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                name = str(payload.get("name", "NodeMCU V3")).strip()
                if not name or len(name) > 80:
                    return self.send_error_json(400, "Enter a device name of up to 80 characters")
                try:
                    rated_watts = clean_float(payload.get("ratedWatts", 60), "ratedWatts", 1, 50000, nullable=False)
                except ValueError as exc:
                    return self.send_error_json(400, str(exc))
                token = secrets.token_urlsafe(32)
                # HomeSense's setup screen represents one physical bulb. Reuse
                # its record on retries instead of adding another "Bulb" each time.
                existing = conn.execute(
                    "SELECT * FROM devices WHERE owner_id=? AND name=? ORDER BY created_at,id LIMIT 1",
                    (user["id"], name),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE devices SET token_hash=?,hardware_id=NULL,rated_watts=?,created_at=? WHERE id=?",
                        (token_hash(token), rated_watts, now_iso(), existing["id"]),
                    )
                    duplicates = conn.execute(
                        "SELECT id,last_seen FROM devices WHERE owner_id=? AND name=? AND id<>?",
                        (user["id"], name, existing["id"]),
                    ).fetchall()
                    for duplicate in duplicates:
                        if duplicate["last_seen"] is not None:
                            continue
                        has_records = conn.execute(
                            "SELECT EXISTS(SELECT 1 FROM readings WHERE device_id=?) "
                            "OR EXISTS(SELECT 1 FROM commands WHERE device_id=?) "
                            "OR EXISTS(SELECT 1 FROM appliance_events WHERE device_id=?) "
                            "OR EXISTS(SELECT 1 FROM notifications WHERE device_id=?)",
                            (duplicate["id"], duplicate["id"], duplicate["id"], duplicate["id"]),
                        ).fetchone()[0]
                        if not has_records:
                            conn.execute("DELETE FROM devices WHERE id=?", (duplicate["id"],))
                    return self.send_json(
                        {"device": {"id": existing["id"], "name": name, "ratedWatts": rated_watts}}
                    )

                device_id = "hs-" + uuid.uuid4().hex[:12]
                conn.execute("INSERT INTO devices(id,owner_id,name,rated_watts,token_hash,created_at) VALUES(?,?,?,?,?,?)", (device_id, user["id"], name, rated_watts, token_hash(token), now_iso()))
                return self.send_json({"device": {"id": device_id, "name": name, "ratedWatts": rated_watts}}, 201)

        if path.startswith("/api/devices/") and path.endswith("/commands"):
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                device_id = path[len("/api/devices/"):-len("/commands")].strip("/")
                update_presence(conn, user["id"])
                device = owned_device(conn, user["id"], device_id)
                if not device:
                    return self.send_error_json(404, "Device not found")
                if not device["online_state"]:
                    return self.send_error_json(409, "This device is offline. Reconnect it before sending a command.")
                state = payload.get("state")
                if not isinstance(state, bool):
                    return self.send_error_json(400, "state must be true or false")
                pending = conn.execute("SELECT id FROM commands WHERE device_id=? AND status IN ('pending','delivered')", (device_id,)).fetchone()
                if pending:
                    return self.send_error_json(409, "A relay command is already in progress")
                command_id = uuid.uuid4().hex
                conn.execute("INSERT INTO commands(id,device_id,user_id,desired_state,created_at) VALUES(?,?,?,?,?)", (command_id, device_id, user["id"], int(state), now_iso()))
                firebase_send_bulb(state)  # NodeMCU picks this up from Firebase /turn
                return self.send_json({"commandId": command_id, "status": "queued"}, 202)

        if path == "/api/notifications/read-all":
            with db() as conn:
                user, _ = self.require_user(conn)
                if not user:
                    return
                conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
                return self.send_json({"updated": True})
        return self.send_error_json(404, "API endpoint not found")

    def api_mutate(self, path, payload):
        with db() as conn:
            user, _ = self.require_user(conn)
            if not user:
                return
            user_id = user["id"]
            if path == "/api/auth/logout":
                cookie = http.cookies.SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                if cookie.get("hs_session"):
                    conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(cookie["hs_session"].value),))
                return self.send_json({"loggedOut": True}, extra={"Set-Cookie": "hs_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            if path == "/api/profile":
                if self.command in ("PUT", "PATCH"):
                    name = str(payload.get("name", "")).strip()
                    if not name or len(name) > 80:
                        return self.send_error_json(400, "Enter a name of up to 80 characters")
                    theme = payload.get("theme", "light")
                    if theme not in ("light", "dark"):
                        return self.send_error_json(400, "Theme must be light or dark")
                    prefs = payload.get("notifications", {})
                    if not isinstance(prefs, dict):
                        return self.send_error_json(400, "Notification preferences are invalid")
                    conn.execute("UPDATE users SET name=?,theme=?,notify_temperature=?,notify_light=?,notify_device=?,notify_appliance=? WHERE id=?",
                                 (name, theme, int(bool(prefs.get("temperature", True))), int(bool(prefs.get("light", True))), int(bool(prefs.get("device", True))), int(bool(prefs.get("appliance", True))), user_id))
                    return self.send_json({"saved": True})
            if path == "/api/profile/password":
                old = str(payload.get("currentPassword", ""))
                new = str(payload.get("newPassword", ""))
                dbuser = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
                if not check_password(old, dbuser["password_hash"]):
                    return self.send_error_json(400, "Current password is incorrect")
                if len(new) < 10 or len(new) > 200:
                    return self.send_error_json(400, "Use a password between 10 and 200 characters")
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(new), user_id))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                secret = http.cookies.SimpleCookie()
                secret.load(self.headers.get("Cookie", ""))
                if secret.get("hs_session"):
                    conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(secret["hs_session"].value),))
                return self.send_json({"passwordChanged": True}, extra={"Set-Cookie": "hs_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            match = re.fullmatch(r"/api/devices/([^/]+)", path)
            if match:
                device = owned_device(conn, user_id, match.group(1))
                if not device:
                    return self.send_error_json(404, "Device not found")
                if self.command == "PATCH":
                    name = str(payload.get("name", "")).strip()
                    if not name or len(name) > 80:
                        return self.send_error_json(400, "Enter a device name of up to 80 characters")
                    try:
                        rated_watts = clean_float(payload.get("ratedWatts", device["rated_watts"]), "ratedWatts", 1, 50000, nullable=False)
                    except ValueError as exc:
                        return self.send_error_json(400, str(exc))
                    conn.execute("UPDATE devices SET name=?,rated_watts=? WHERE id=?", (name, rated_watts, device["id"]))
                    return self.send_json({"saved": True, "ratedWatts": rated_watts})
                if self.command == "DELETE":
                    conn.execute("DELETE FROM devices WHERE id=?", (device["id"],))
                    return self.send_json({"deleted": True})
            match = re.fullmatch(r"/api/notifications/(\d+)", path)
            if match and self.command == "PATCH":
                is_read = payload.get("read")
                if not isinstance(is_read, bool):
                    return self.send_error_json(400, "read must be true or false")
                conn.execute("UPDATE notifications SET is_read=? WHERE id=? AND user_id=?", (int(is_read), int(match.group(1)), user_id))
                return self.send_json({"updated": True})
            if path == "/api/notifications" and self.command == "DELETE":
                conn.execute("DELETE FROM notifications WHERE user_id=?", (user_id,))
                return self.send_json({"deleted": True})
            return self.send_error_json(404, "API endpoint not found")


AUTH_ATTEMPTS = {}


def allow_auth_attempt(ip):
    now = time.time()
    attempts = [t for t in AUTH_ATTEMPTS.get(ip, []) if now - t < 600]
    if len(attempts) >= 10:
        AUTH_ATTEMPTS[ip] = attempts
        return False
    attempts.append(now)
    AUTH_ATTEMPTS[ip] = attempts
    return True


def reset_auth_attempts(ip):
    AUTH_ATTEMPTS.pop(ip, None)


FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "https://iotbasedbulbonoff-default-rtdb.firebaseio.com").rstrip("/")
FIREBASE_AUTH = os.environ.get("FIREBASE_AUTH", "")


def firebase_call(method, path, value=None):
    """Tiny Firebase Realtime Database REST helper. Returns parsed JSON or None on failure."""
    url = f"{FIREBASE_DB_URL}/{path}.json" + (f"?auth={FIREBASE_AUTH}" if FIREBASE_AUTH else "")
    data = None if value is None else json.dumps(value).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read() or b"null")
    except Exception as exc:
        print(f"Firebase {method} {path} failed: {exc}", file=sys.stderr)
        return None


def firebase_send_bulb(state):
    """Website -> Firebase: the NodeMCU polls /turn and switches the relay."""
    firebase_call("PUT", "turn", "on" if state else "off")


def firebase_sync_once():
    """Firebase -> website: copy the NodeMCU's /status into the readings tables."""
    status = firebase_call("GET", "status")
    if not isinstance(status, dict) or not isinstance(status.get("updatedAt"), (int, float)):
        return
    seen = datetime.fromtimestamp(status["updatedAt"] / 1000, timezone.utc)
    if (now_dt() - seen).total_seconds() > int(CFG["online_timeout_seconds"]):
        return
    stamp = seen.isoformat(timespec="seconds")
    relay = status.get("relayOn") is True
    try:
        temp = clean_float(status.get("temperature"), "temperature", -40, 80)
        humidity = clean_float(status.get("humidity"), "humidity", 0, 100)
    except ValueError:
        return
    raw = status.get("ldrRaw")
    raw = raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 1023 else None
    rssi = status.get("wifiRssi")
    rssi = rssi if isinstance(rssi, int) and -127 <= rssi <= 0 else None
    pct = level = None
    if raw is not None:
        dark, bright = int(CFG["ldr_dark_raw"]), int(CFG["ldr_bright_raw"])
        pct = round(max(0, min(100, (raw - dark) * 100 / (bright - dark))), 2)
        level = "Low" if pct < 33 else ("Medium" if pct < 67 else "High")
    with db() as conn:
        # One physical bulb: if nobody has a "Bulb" yet, give it to the first (owner) account.
        if not conn.execute("SELECT 1 FROM devices WHERE name='Bulb' LIMIT 1").fetchone():
            owner = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if owner:
                conn.execute("INSERT INTO devices(id,owner_id,name,rated_watts,token_hash,created_at) VALUES(?,?,?,?,?,?)",
                             ("fb-" + uuid.uuid4().hex[:12], owner["id"], "Bulb", 60, token_hash(secrets.token_urlsafe(32)), now_iso()))
        for device in conn.execute("SELECT * FROM devices WHERE name='Bulb'").fetchall():
            if device["last_seen"] and device["last_seen"] >= stamp:
                continue  # nothing new from the NodeMCU
            conn.execute("INSERT INTO readings(device_id,temperature,humidity,ldr_raw,light_pct,light_level,power_w,relay_on,wifi_rssi,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (device["id"], temp, humidity, raw, pct, level, None, int(relay), rssi, stamp))
            conn.execute("UPDATE devices SET last_seen=?,online_state=1,relay_on=?,current_power_w=NULL,wifi_rssi=?,firmware_version='firebase',ip_address='Firebase' WHERE id=?",
                         (stamp, int(relay), rssi, device["id"]))
            if device["relay_on"] is None or bool(device["relay_on"]) != relay:
                conn.execute("INSERT INTO appliance_events(device_id,user_id,state,source,created_at) VALUES(?,?,?,?,?)",
                             (device["id"], device["owner_id"], int(relay), "web", stamp))
                notify(conn, device["owner_id"], device["id"], "appliance", "Bulb switched on" if relay else "Bulb switched off", f"{device['name']} reports the bulb {'on' if relay else 'off'}.")
            # The NodeMCU confirmed the relay state: finish matching commands, expire stale ones.
            conn.execute("UPDATE commands SET status='complete',completed_at=? WHERE device_id=? AND status IN ('pending','delivered') AND desired_state=?",
                         (now_iso(), device["id"], int(relay)))
            expire = (now_dt() - timedelta(minutes=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE commands SET status='failed',completed_at=?,error='NodeMCU did not confirm in time' WHERE device_id=? AND status IN ('pending','delivered') AND created_at<?",
                         (now_iso(), device["id"], expire))


def firebase_sync_loop():
    while True:
        try:
            firebase_sync_once()
        except Exception as exc:
            print(f"Firebase sync error: {exc}", file=sys.stderr)
        time.sleep(3)


def run():
    threading.Thread(target=firebase_sync_loop, daemon=True).start()
    host = CFG["host"]
    port = CFG["port"]
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    try:
        httpd = Server((host, port), Handler)
    except OSError as exc:
        in_use = exc.errno in (98, 10048) or getattr(exc, "winerror", None) == 10048
        if not in_use:
            raise
        requested_port = port
        httpd = None
        for candidate in range(requested_port + 1, min(65536, requested_port + 11)):
            try:
                httpd = Server((host, candidate), Handler)
                CFG["port"] = candidate
                print(f"Port {requested_port} is already in use; HomeSense selected {candidate}.")
                break
            except OSError as fallback_exc:
                if not (fallback_exc.errno in (98, 10048) or getattr(fallback_exc, "winerror", None) == 10048):
                    raise
        if httpd is None:
            raise OSError(f"Ports {requested_port} through {min(65535, requested_port + 10)} are unavailable") from exc
    port = CFG["port"]
    print(f"HomeSense running at http://{host}:{port}")
    print(f"Database: {CFG['database_path']}")
    print("Open the website URL above, then connect the NodeMCU to Wi-Fi from Connections.")
    print("Press Ctrl+C to stop the web service.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping HomeSense…")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
