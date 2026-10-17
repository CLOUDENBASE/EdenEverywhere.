#!/usr/bin/env python3
"""EdenEverywhere cloud master — the always-on hub for the collective.

Every phone runs its own server for offline/LAN sharing; this master
aggregates everything when phones have internet. Same JSON API as the
Android embedded server, so the app talks to both identically.

Run locally:  python3 server.py            (listens on :8080)
Deploy:       see Dockerfile + render.yaml (one-click on Render)
"""
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

DB_PATH = os.environ.get("EE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "eden.db"))
PORT = int(os.environ.get("PORT", "8080"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS spots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, muuid TEXT UNIQUE,
  fruit TEXT, variety TEXT, lat REAL, lng REAL, city TEXT, land TEXT,
  owner TEXT, season TEXT, notes TEXT, photo TEXT, by_name TEXT,
  anon INTEGER, approx INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS closed (
  spot_id INTEGER PRIMARY KEY, until_date TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS vibes (
  spot_id INTEGER, tag TEXT, n INTEGER, PRIMARY KEY (spot_id, tag));
CREATE TABLE IF NOT EXISTS quality (
  spot_id INTEGER PRIMARY KEY, qsum INTEGER, qn INTEGER);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, muuid TEXT UNIQUE,
  spot_id INTEGER, date TEXT, time TEXT, details TEXT, by_name TEXT,
  base INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS rsvps (
  event_id INTEGER PRIMARY KEY, going INTEGER);
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, muuid TEXT UNIQUE,
  item TEXT, price TEXT, details TEXT, by_name TEXT, photo TEXT,
  spot_id INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, muuid TEXT UNIQUE,
  by_name TEXT, ts INTEGER, text TEXT, photo TEXT, mtype TEXT, likes INTEGER);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER,
  by_name TEXT, text TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS bios (
  name TEXT PRIMARY KEY, bio TEXT, pubkey TEXT, wallet TEXT, anon INTEGER DEFAULT 0, photo TEXT);
CREATE TABLE IF NOT EXISTS dms (
  id INTEGER PRIMARY KEY AUTOINCREMENT, muuid TEXT UNIQUE,
  sender TEXT, recipient TEXT, iv TEXT, ctext TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS dmfiles (
  muuid TEXT PRIMARY KEY, sender TEXT, recipient TEXT,
  iv TEXT, ctext TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS announcements (
  muuid TEXT PRIMARY KEY, by_name TEXT, text TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS channel_items (
  muuid TEXT PRIMARY KEY, kind TEXT, title TEXT, text TEXT,
  media TEXT, link TEXT, by_name TEXT, ts INTEGER, live INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS presence (
  name TEXT PRIMARY KEY, lat REAL, lng REAL, ts INTEGER, anon INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dmfile_chunks (
  file_muuid TEXT, idx INTEGER, data TEXT, PRIMARY KEY(file_muuid, idx));
CREATE TABLE IF NOT EXISTS dmfiles (
  muuid TEXT PRIMARY KEY, sender TEXT, recipient TEXT,
  iv TEXT, ctext TEXT, ts INTEGER, chunked INTEGER DEFAULT 0, chunks INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dmreactions (
  msg_muuid TEXT, emoji TEXT, name TEXT, ts INTEGER,
  PRIMARY KEY(msg_muuid, emoji, name));
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for _ddl in ("ALTER TABLE bios ADD COLUMN anon INTEGER DEFAULT 0",
                 "ALTER TABLE bios ADD COLUMN pubkey TEXT",
                 "ALTER TABLE bios ADD COLUMN wallet TEXT",
                 "ALTER TABLE dmfiles ADD COLUMN chunked INTEGER DEFAULT 0",
                 "ALTER TABLE dmfiles ADD COLUMN chunks INTEGER DEFAULT 0",
                 "ALTER TABLE bios ADD COLUMN photo TEXT",
                 "ALTER TABLE channel_items ADD COLUMN live INTEGER DEFAULT 0"):
        try:
            conn.execute(_ddl)
        except Exception:
            pass
    return conn


def by_muuid(conn, table, muuid):
    if not muuid:
        return None
    r = conn.execute(f"SELECT id FROM {table} WHERE muuid=?", (muuid,)).fetchone()
    return r["id"] if r else None


def spot_json(conn, row):
    s = dict(row)
    sid = s["id"]
    closed = conn.execute("SELECT until_date, note FROM closed WHERE spot_id=?", (sid,)).fetchone()
    s["closed"] = {"until": closed["until_date"], "note": closed["note"]} if closed else None
    s["vibes"] = {r["tag"]: r["n"] for r in conn.execute("SELECT tag, n FROM vibes WHERE spot_id=?", (sid,))}
    q = conn.execute("SELECT qsum, qn FROM quality WHERE spot_id=?", (sid,)).fetchone()
    s["quality"] = {"sum": q["qsum"], "n": q["qn"]} if q else None
    s["by"] = s.pop("by_name")
    return s


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            n = 0
        # cap at ~15MB (photo uploads)
        n = min(n, 15 * 1024 * 1024)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        conn = db()
        try:
            if parsed.path == "/api/channel":
                conn.execute("DELETE FROM channel_items WHERE muuid=?", ((q.get("id") or [None])[0],))
                conn.commit()
                self._send({"ok": True})
            elif parsed.path == "/api/presence":
                conn.execute("DELETE FROM presence WHERE name=?", ((q.get("name") or [None])[0],))
                conn.commit()
                self._send({"ok": True})
            else:
                self._send({"error": "unknown route"}, 404)
        finally:
            conn.close()

    def do_GET(self):
        p = urlparse(self.path).path
        conn = db()
        try:
            if p == "/":
                self._send({"ok": True, "service": "edeneverywhere-cloud"})
            elif p == "/api/spots":
                self._send([spot_json(conn, r) for r in conn.execute("SELECT * FROM spots ORDER BY id")])
            elif p == "/api/events":
                out = []
                for r in conn.execute(
                        "SELECT e.*, IFNULL(v.going,0) AS rsvps FROM events e "
                        "LEFT JOIN rsvps v ON v.event_id=e.id ORDER BY e.date"):
                    d = dict(r)
                    d["by"] = d.pop("by_name")
                    out.append(d)
                self._send(out)
            elif p == "/api/listings":
                out = []
                for r in conn.execute("SELECT * FROM listings ORDER BY id DESC"):
                    d = dict(r)
                    d["by"] = d.pop("by_name")
                    out.append(d)
                self._send(out)
            elif p == "/api/posts":
                out = []
                for r in conn.execute("SELECT * FROM posts ORDER BY ts DESC"):
                    d = dict(r)
                    d["by"] = d.pop("by_name")
                    d["comments"] = [
                        {"by": c["by_name"], "text": c["text"], "ts": c["ts"]}
                        for c in conn.execute(
                            "SELECT by_name, text, ts FROM comments WHERE post_id=? ORDER BY ts", (d["id"],))
                    ]
                    out.append(d)
                self._send(out)
            elif p == "/api/bios":
                try:
                    rows = conn.execute("SELECT name, bio, pubkey, wallet, anon, photo FROM bios")
                    self._send([{"name": r["name"], "bio": r["bio"],
                                 **({"pubkey": r["pubkey"]} if r["pubkey"] else {}),
                                 **({"wallet": r["wallet"]} if r["wallet"] else {}),
                                 "anon": r["anon"] or 0,
                                 **({"photo": r["photo"]} if r["photo"] else {})} for r in rows])
                except Exception:
                    try:
                        rows = conn.execute("SELECT name, bio, pubkey FROM bios")
                        self._send([{"name": r["name"], "bio": r["bio"], **({"pubkey": r["pubkey"]} if r["pubkey"] else {})} for r in rows])
                    except Exception:
                        self._send([{"name": r["name"], "bio": r["bio"]} for r in conn.execute("SELECT name, bio FROM bios")])
            elif p == "/api/dms":
                self._send([{"muuid": r["muuid"], "from": r["sender"], "to": r["recipient"],
                             "iv": r["iv"], "ct": r["ctext"], "ts": r["ts"]}
                            for r in conn.execute("SELECT muuid, sender, recipient, iv, ctext, ts FROM dms ORDER BY ts ASC")])
            elif p == "/api/dmreactions":
                self._send([{"muuid": r["msg_muuid"], "emoji": r["emoji"], "name": r["name"], "on": 1, "ts": r["ts"]}
                            for r in conn.execute("SELECT msg_muuid, emoji, name, ts FROM dmreactions ORDER BY ts ASC")])
            elif p == "/api/announcements":
                self._send([{"muuid": r["muuid"], "by": r["by_name"], "text": r["text"], "ts": r["ts"]}
                            for r in conn.execute("SELECT muuid, by_name, text, ts FROM announcements ORDER BY ts DESC LIMIT 50")])
            elif p == "/api/presence":
                conn.execute("DELETE FROM presence WHERE ts<?", (int(time.time() * 1000) - 600000,))
                conn.commit()
                self._send([{"name": r["name"], "lat": r["lat"], "lng": r["lng"], "ts": r["ts"], "anon": r["anon"] or 0}
                            for r in conn.execute("SELECT name, lat, lng, ts, anon FROM presence")])
            elif p == "/api/channel":
                _ch = []
                for r in conn.execute("SELECT muuid, kind, title, text, media, link, by_name, ts, live FROM channel_items ORDER BY ts DESC LIMIT 100"):
                    _it = {"muuid": r["muuid"], "kind": r["kind"], "title": r["title"], "text": r["text"],
                           "media": r["media"], "link": r["link"], "by": r["by_name"], "ts": r["ts"]}
                    if r["live"]:
                        _it["live"] = 1
                    _ch.append(_it)
                self._send(_ch)
            elif p == "/api/dmfiles":
                q = parse_qs(urlparse(self.path).query)
                fid = (q.get("id") or [None])[0]
                asname = (q.get("as") or [None])[0]
                r = conn.execute("SELECT muuid, sender, recipient, iv, ctext, ts, chunked, chunks FROM dmfiles WHERE muuid=?",
                                 (fid,)).fetchone()
                if r and asname in (r["sender"], r["recipient"]):
                    self._send({"muuid": r["muuid"], "from": r["sender"], "to": r["recipient"],
                                "iv": r["iv"], "ct": r["ctext"], "ts": r["ts"],
                                "chunked": r["chunked"] or 0, "chunks": r["chunks"] or 0})
                else:
                    self._send({})
            elif p == "/api/dmfile_chunks":
                q = parse_qs(urlparse(self.path).query)
                f = (q.get("file") or [None])[0]
                asname = (q.get("as") or [None])[0]
                idx = (q.get("idx") or [None])[0]
                m = conn.execute("SELECT sender, recipient FROM dmfiles WHERE muuid=?", (f,)).fetchone()
                if not (m and asname in (m["sender"], m["recipient"])):
                    self._send({"error": "denied"})
                elif idx is not None:
                    r = conn.execute("SELECT data FROM dmfile_chunks WHERE file_muuid=? AND idx=?",
                                     (f, int(idx))).fetchone()
                    self._send({"data": r["data"]} if r else {"error": "missing"})
                else:
                    self._send({"have": [r["idx"] for r in
                                         conn.execute("SELECT idx FROM dmfile_chunks WHERE file_muuid=? ORDER BY idx", (f,))]})
            elif p == "/api/app-version":
                try:
                    with open(os.path.join(os.path.dirname(__file__), "version.json")) as f:
                        self._send(json.load(f))
                except Exception:
                    self._send({"version": 0})
            elif p == "/api/stats":
                self._send({t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                            for t in ("spots", "events", "listings", "posts", "comments")})
            else:
                self._send({"error": "not found"}, 404)
        finally:
            conn.close()

    def do_POST(self):
        p = urlparse(self.path).path
        b = self._body()
        conn = db()
        try:
            if p == "/api/spots":
                eid = by_muuid(conn, "spots", b.get("muuid"))
                if eid is None:
                    cur = conn.execute(
                        "INSERT INTO spots (muuid, fruit, variety, lat, lng, city, land, owner, season,"
                        " notes, photo, by_name, anon, approx, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (b.get("muuid"), b.get("fruit", ""), b.get("variety", ""), b.get("lat", 0),
                         b.get("lng", 0), b.get("city", ""), b.get("land", "public"), b.get("owner", ""),
                         b.get("season", ""), b.get("notes", ""), b.get("photo", ""),
                         b.get("by", "Anonymous picker"), 1 if b.get("anon") else 0,
                         1 if b.get("approx") else 0, int(time.time() * 1000)))
                    eid = cur.lastrowid
                conn.commit()
                self._send({"id": eid})
            elif p == "/api/spots/closed":
                sid = b.get("spot_id")
                if not b.get("until") and not b.get("note"):
                    conn.execute("DELETE FROM closed WHERE spot_id=?", (sid,))
                else:
                    conn.execute("INSERT OR REPLACE INTO closed (spot_id, until_date, note) VALUES (?,?,?)",
                                 (sid, b.get("until", ""), b.get("note", "")))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/vibes":
                conn.execute(
                    "INSERT INTO vibes (spot_id, tag, n) VALUES (?,?,MAX(0, COALESCE("
                    "(SELECT n FROM vibes WHERE spot_id=? AND tag=?),0)+?))"
                    " ON CONFLICT(spot_id, tag) DO UPDATE SET n=excluded.n",
                    (b.get("spot_id"), b.get("tag"), b.get("spot_id"), b.get("tag"), b.get("delta", 1)))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/quality":
                conn.execute(
                    "INSERT INTO quality (spot_id, qsum, qn) VALUES (?,?,?)"
                    " ON CONFLICT(spot_id) DO UPDATE SET"
                    " qsum=MAX(0, qsum+?), qn=MAX(0, qn+?)",
                    (b.get("spot_id"), max(0, b.get("delta_sum", 0)), max(0, b.get("delta_n", 0)),
                     b.get("delta_sum", 0), b.get("delta_n", 0)))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/events":
                eid = by_muuid(conn, "events", b.get("muuid"))
                if eid is None:
                    cur = conn.execute(
                        "INSERT INTO events (muuid, spot_id, date, time, details, by_name, base, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (b.get("muuid"), b.get("spot_id", -1), b.get("date", ""), b.get("time", ""),
                         b.get("details", ""), b.get("by", "Anonymous host"), b.get("base", 0),
                         int(time.time() * 1000)))
                    eid = cur.lastrowid
                    conn.execute("INSERT OR IGNORE INTO rsvps (event_id, going) VALUES (?,0)", (eid,))
                conn.commit()
                self._send({"id": eid})
            elif p == "/api/rsvp":
                conn.execute(
                    "INSERT INTO rsvps (event_id, going) VALUES (?, MAX(0, ?))"
                    " ON CONFLICT(event_id) DO UPDATE SET going=MAX(0, going+?)",
                    (b.get("event_id"), b.get("delta", 1), b.get("delta", 1)))
                conn.commit()
                r = conn.execute("SELECT going FROM rsvps WHERE event_id=?", (b.get("event_id"),)).fetchone()
                self._send({"going": r["going"] if r else 0})
            elif p == "/api/listings":
                eid = by_muuid(conn, "listings", b.get("muuid"))
                if eid is None:
                    cur = conn.execute(
                        "INSERT INTO listings (muuid, item, price, details, by_name, photo, spot_id, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (b.get("muuid"), b.get("item", "Fresh produce"), b.get("price", ""),
                         b.get("details", ""), b.get("by", "Anonymous grower"), b.get("photo", ""),
                         b.get("spot_id", -1), int(time.time() * 1000)))
                    eid = cur.lastrowid
                conn.commit()
                self._send({"id": eid})
            elif p == "/api/posts":
                eid = by_muuid(conn, "posts", b.get("muuid"))
                if eid is None:
                    cur = conn.execute(
                        "INSERT INTO posts (muuid, by_name, ts, text, photo, mtype, likes)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (b.get("muuid"), b.get("by", "Anonymous picker"), b.get("ts", int(time.time() * 1000)),
                         b.get("text", ""), b.get("photo", ""), b.get("mtype", "image"), 0))
                    eid = cur.lastrowid
                conn.commit()
                self._send({"id": eid})
            elif p == "/api/like":
                conn.execute("UPDATE posts SET likes=MAX(0, likes+?) WHERE id=?",
                             (b.get("delta", 1), b.get("post_id")))
                conn.commit()
                r = conn.execute("SELECT likes FROM posts WHERE id=?", (b.get("post_id"),)).fetchone()
                self._send({"likes": r["likes"] if r else 0})
            elif p == "/api/comments":
                cur = conn.execute(
                    "INSERT INTO comments (post_id, by_name, text, ts) VALUES (?,?,?,?)",
                    (b.get("post_id"), b.get("by", "You"), b.get("text", ""), int(time.time() * 1000)))
                conn.commit()
                self._send({"id": cur.lastrowid})
            elif p == "/api/bios":
                try:
                    _keep = conn.execute("SELECT pubkey, wallet, photo FROM bios WHERE name=?",
                                         (b.get("name"),)).fetchone()
                    conn.execute("INSERT OR REPLACE INTO bios (name, bio, pubkey, wallet, anon, photo) VALUES (?,?,?,?,?,?)",
                                 (b.get("name"), b.get("bio", ""),
                                  b.get("pubkey") if b.get("pubkey") is not None else (_keep["pubkey"] if _keep else None),
                                  b.get("wallet") if b.get("wallet") is not None else (_keep["wallet"] if _keep else None),
                                  b.get("anon", 0),
                                  b.get("photo") if b.get("photo") is not None else (_keep["photo"] if _keep else None)))
                except Exception:
                    try: conn.execute("ALTER TABLE bios ADD COLUMN pubkey TEXT")
                    except Exception: pass
                    try: conn.execute("ALTER TABLE bios ADD COLUMN wallet TEXT")
                    except Exception: pass
                    try:
                        conn.execute("INSERT OR REPLACE INTO bios (name, bio, pubkey, wallet) VALUES (?,?,?,?)",
                                     (b.get("name"), b.get("bio", ""), b.get("pubkey"), b.get("wallet")))
                    except Exception:
                        conn.execute("INSERT OR REPLACE INTO bios (name, bio) VALUES (?,?)",
                                     (b.get("name"), b.get("bio", "")))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/dms":
                if b.get("muuid") and conn.execute("SELECT id FROM dms WHERE muuid=?", (b.get("muuid"),)).fetchone():
                    self._send({"id": -1, "dup": True})
                else:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO dms (muuid, sender, recipient, iv, ctext, ts) VALUES (?,?,?,?,?,?)",
                        (b.get("muuid"), b.get("from", ""), b.get("to", ""), b.get("iv", ""),
                         b.get("ct", ""), b.get("ts", int(time.time() * 1000))))
                    conn.commit()
                    self._send({"id": cur.lastrowid})
            elif p == "/api/dmreactions":
                muuid, emoji, name = b.get("muuid", ""), b.get("emoji", ""), b.get("name", "")
                if muuid and emoji and name:
                    if b.get("on", 1):
                        conn.execute(
                            "INSERT OR REPLACE INTO dmreactions (msg_muuid, emoji, name, ts) VALUES (?,?,?,?)",
                            (muuid, emoji, name, b.get("ts", int(time.time() * 1000))))
                    else:
                        conn.execute(
                            "DELETE FROM dmreactions WHERE msg_muuid=? AND emoji=? AND name=?",
                            (muuid, emoji, name))
                    conn.commit()
                self._send({"ok": True})
            elif p == "/api/dmfiles":
                conn.execute(
                    "INSERT OR IGNORE INTO dmfiles (muuid, sender, recipient, iv, ctext, ts, chunked, chunks) VALUES (?,?,?,?,?,?,?,?)",
                    (b.get("muuid"), b.get("from", ""), b.get("to", ""), b.get("iv", ""),
                     b.get("ct", ""), b.get("ts", int(time.time() * 1000)),
                     b.get("chunked", 0), b.get("chunks", 0)))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/dmfile_chunks":
                m = conn.execute("SELECT sender, recipient FROM dmfiles WHERE muuid=?",
                                 (b.get("file"),)).fetchone()
                if m and b.get("from") == m["sender"] and b.get("idx") is not None and b.get("idx") >= 0:
                    conn.execute(
                        "INSERT OR REPLACE INTO dmfile_chunks (file_muuid, idx, data) VALUES (?,?,?)",
                        (b.get("file"), b.get("idx"), b.get("data", "")))
                    conn.commit()
                    self._send({"ok": True})
                else:
                    self._send({"error": "denied"})
            elif p == "/api/announcements":
                conn.execute(
                    "INSERT OR IGNORE INTO announcements (muuid, by_name, text, ts) VALUES (?,?,?,?)",
                    (b.get("muuid"), b.get("by", ""), b.get("text", ""), b.get("ts", int(time.time() * 1000))))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/presence":
                if b.get("remove"):
                    conn.execute("DELETE FROM presence WHERE name=?", (b.get("name"),))
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO presence (name, lat, lng, ts, anon) VALUES (?,?,?,?,?)",
                        (b.get("name"), b.get("lat", 0), b.get("lng", 0), int(time.time() * 1000), b.get("anon", 0)))
                conn.commit()
                self._send({"ok": True})
            elif p == "/api/channel":
                conn.execute(
                    "INSERT OR IGNORE INTO channel_items (muuid, kind, title, text, media, link, by_name, ts, live) VALUES (?,?,?,?,?,?,?,?,?)",
                    (b.get("muuid"), b.get("kind", "text"), b.get("title", ""), b.get("text", ""),
                     b.get("media", ""), b.get("link", ""), b.get("by", ""), b.get("ts", int(time.time() * 1000)),
                     b.get("live", 0)))
                conn.commit()
                self._send({"ok": True})
            else:
                self._send({"error": "not found"}, 404)
        finally:
            conn.close()


if __name__ == "__main__":
    db().close()  # ensure schema exists
    print(f"EdenEverywhere cloud master on :{PORT}  (db: {DB_PATH})", flush=True)
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
