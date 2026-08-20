# YUGITO Auth Server 1.1.8
# Google = identite canonique. Le pseudo/account_id est sauvegarde dans le
# dossier appData Google Drive de l'utilisateur afin de survivre aux redemarrages
# du Web Service Render Free (filesystem ephemere).
#
# Variables requises : GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
# Facultatives : YUGITO_PUBLIC_BASE_URL, YUGITO_SESSION_SECRET, YUGITO_AUTH_DB
# Google Cloud : activer Google Drive API pour le meme projet OAuth.

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.getenv("YUGITO_AUTH_DB", "yugito_auth.sqlite3")
PUBLIC_BASE = (os.getenv("YUGITO_PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET = (os.getenv("YUGITO_SESSION_SECRET") or GOOGLE_CLIENT_SECRET or "").encode("utf-8")
PORT = int(os.getenv("PORT", "8787"))
SESSION_TTL = 60 * 60 * 24 * 30
DEVICE_TTL = 10 * 60
DRIVE_FILE_NAME = "yugito_identity_v1.json"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"
IDENTITY_NAMESPACE = "yugito/v40-21/identity"
IDENTITY_APP_KEY = b"YUGITO-V40-21-IDENTITY-REGISTRY-2026"
MQTT_HOST = os.getenv("YUGITO_MQTT_HOST", "broker.emqx.io")
MQTT_PORT = int(os.getenv("YUGITO_MQTT_PORT", "8883"))


def now() -> int:
    return int(time.time())


def normalize_pseudo(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    return " ".join(value.split()).casefold()


def normalize_email(value: str) -> str:
    return str(value or "").strip().casefold()


def validate_pseudo(value: str):
    p = unicodedata.normalize("NFKC", str(value or "")).strip()
    p = " ".join(p.split())
    if len(p) < 3:
        return False, "Ton pseudo doit contenir au moins 3 caractères.", p
    if len(p) > 20:
        return False, "Ton pseudo ne peut pas dépasser 20 caractères.", p
    if p[0] in "-_" or p[-1] in "-_":
        return False, "Le pseudo ne peut pas commencer ou finir par - ou _.", p
    for ch in p:
        if ch.isalnum() or ch in "-_ ":
            continue
        return False, "Utilise uniquement lettres, chiffres, espaces, - ou _.", p
    return True, "", p


def db():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts(
          account_id TEXT PRIMARY KEY,
          google_sub TEXT UNIQUE NOT NULL,
          email TEXT,
          display_name TEXT,
          picture TEXT,
          pseudo TEXT UNIQUE,
          pseudo_norm TEXT UNIQUE,
          elo INTEGER NOT NULL DEFAULT 100,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          legacy_account_id TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          google_access_token TEXT,
          google_expires_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS devices(
          device_code TEXT PRIMARY KEY,
          platform TEXT,
          created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          account_id TEXT,
          session_token TEXT,
          consumed INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    # migrations pour anciennes bases SQLite
    scols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
    if "google_access_token" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN google_access_token TEXT")
    if "google_expires_at" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN google_expires_at INTEGER NOT NULL DEFAULT 0")
    acols = {r[1] for r in c.execute("PRAGMA table_info(accounts)").fetchall()}
    if "elo" not in acols:
        c.execute("ALTER TABLE accounts ADD COLUMN elo INTEGER NOT NULL DEFAULT 100")
    c.commit()
    return c


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    value = str(value or "")
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def _session_payload(row) -> dict:
    return {
        "v": 1,
        "sub": str(row["google_sub"] or ""),
        "aid": str(row["account_id"] or ""),
        "pseudo": str(row["pseudo"] or ""),
        "email": str(row["email"] or ""),
        "name": str(row["display_name"] or ""),
        "picture": str(row["picture"] or ""),
        "elo": int(row["elo"] or 100),
        "iat": now(),
        "exp": now() + SESSION_TTL,
    }


def issue_session(conn, row, google_access_token: str = "", google_expires_at: int = 0) -> str:
    if not SESSION_SECRET:
        # compatibilite de secours, mais Google client secret est normalement defini
        token = secrets.token_urlsafe(48)
    else:
        body = _b64e(json_bytes(_session_payload(row)))
        sig = _b64e(hmac.new(SESSION_SECRET, body.encode("ascii"), hashlib.sha256).digest())
        token = "ys1." + body + "." + sig
    conn.execute(
        "INSERT OR REPLACE INTO sessions(token_hash,account_id,created_at,expires_at,google_access_token,google_expires_at) VALUES(?,?,?,?,?,?)",
        (token_hash(token), row["account_id"], now(), now() + SESSION_TTL, str(google_access_token or ""), int(google_expires_at or 0)),
    )
    conn.commit()
    return token


def verify_signed_session(token: str):
    if not SESSION_SECRET or not str(token).startswith("ys1."):
        return None
    try:
        _, body, sig = str(token).split(".", 2)
        expected = _b64e(hmac.new(SESSION_SECRET, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
        if int(payload.get("exp") or 0) <= now():
            return None
        if not payload.get("sub") or not payload.get("aid"):
            return None
        return payload
    except Exception:
        return None


def _upsert_from_signed_payload(conn, payload: dict):
    sub = str(payload.get("sub") or "")
    aid = str(payload.get("aid") or "")
    row = conn.execute("SELECT * FROM accounts WHERE google_sub=?", (sub,)).fetchone()
    if row:
        # Si la base ephemere a un compte provisoire mais que le token signe connait
        # l'ancien account_id, on restaure l'identite canonique quand il n'y a pas de clash.
        if str(row["account_id"]) != aid:
            clash = conn.execute("SELECT google_sub FROM accounts WHERE account_id=?", (aid,)).fetchone()
            if not clash:
                old = str(row["account_id"])
                conn.execute("UPDATE accounts SET account_id=? WHERE account_id=?", (aid, old))
                conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?", (aid, old))
                conn.execute("UPDATE devices SET account_id=? WHERE account_id=?", (aid, old))
        conn.execute(
            "UPDATE accounts SET email=?,display_name=?,picture=?,pseudo=COALESCE(NULLIF(pseudo,''),?),pseudo_norm=COALESCE(NULLIF(pseudo_norm,''),?),elo=MAX(elo,?),updated_at=? WHERE google_sub=?",
            (
                str(payload.get("email") or ""), str(payload.get("name") or ""), str(payload.get("picture") or ""),
                str(payload.get("pseudo") or ""), normalize_pseudo(payload.get("pseudo")) if payload.get("pseudo") else None,
                int(payload.get("elo") or 100), now(), sub,
            ),
        )
    else:
        t = now()
        pseudo = str(payload.get("pseudo") or "") or None
        pn = normalize_pseudo(pseudo) if pseudo else None
        try:
            conn.execute(
                "INSERT INTO accounts(account_id,google_sub,email,display_name,picture,pseudo,pseudo_norm,elo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (aid, sub, str(payload.get("email") or ""), str(payload.get("name") or ""), str(payload.get("picture") or ""), pseudo, pn, int(payload.get("elo") or 100), t, t),
            )
        except sqlite3.IntegrityError:
            # Le pseudo/account_id a deja ete restaure par une autre session : on
            # retombe sur la ligne Google si elle existe.
            pass
    conn.commit()
    return conn.execute("SELECT * FROM accounts WHERE google_sub=?", (sub,)).fetchone()


def auth_context(headers):
    hdr = str(headers.get("Authorization") or "")
    if not hdr.startswith("Bearer "):
        return None
    token = hdr[7:].strip()
    if not token:
        return None
    conn = db()
    signed = verify_signed_session(token)
    if signed:
        row = _upsert_from_signed_payload(conn, signed)
        sess = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (token_hash(token),)).fetchone()
        out = {"account": dict(row) if row else None, "token": token, "session": dict(sess) if sess else {}, "signed": signed}
        conn.close()
        return out if out["account"] else None
    sess = conn.execute("SELECT * FROM sessions WHERE token_hash=? AND expires_at>?", (token_hash(token), now())).fetchone()
    if not sess:
        conn.close()
        return None
    row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (sess["account_id"],)).fetchone()
    out = {"account": dict(row) if row else None, "token": token, "session": dict(sess), "signed": None}
    conn.close()
    return out if out["account"] else None


def auth_account(headers):
    ctx = auth_context(headers)
    return ctx["account"] if ctx else None


def account_public(row, *, recovery_token: str = ""):
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    out = {
        "account_id": str(row.get("account_id") or ""),
        "pseudo": str(row.get("pseudo") or ""),
        "email": str(row.get("email") or ""),
        "name": str(row.get("display_name") or ""),
        "picture": str(row.get("picture") or ""),
        "google_linked": True,
        "elo": int(row.get("elo") or 100),
    }
    if recovery_token:
        out["recovery_token"] = str(recovery_token)
    return out


def google_exchange(code: str):
    redirect_uri = PUBLIC_BASE + "/oauth/callback"
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        tok = json.loads(r.read().decode())
    id_token = tok.get("id_token")
    if not id_token:
        raise RuntimeError("Google n'a pas renvoyé d'ID token.")
    with urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(id_token), timeout=15) as r:
        claims = json.loads(r.read().decode())
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        raise RuntimeError("Audience Google invalide.")
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise RuntimeError("Émetteur Google invalide.")
    if int(claims.get("exp") or 0) <= now():
        raise RuntimeError("Token Google expiré.")
    if str(claims.get("email_verified", "true")).lower() not in ("true", "1"):
        raise RuntimeError("Adresse Google non vérifiée.")
    return claims, tok


def _google_json(url: str, access_token: str, *, method: str = "GET", body: bytes | None = None, content_type: str = "application/json"):
    headers = {"Authorization": "Bearer " + str(access_token), "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8") or "{}") if raw else {}


def drive_find_identity(access_token: str) -> str:
    q = urllib.parse.urlencode({
        "spaces": "appDataFolder",
        "q": f"name='{DRIVE_FILE_NAME}' and trashed=false",
        "fields": "files(id,name)",
        "pageSize": "1",
    })
    data = _google_json("https://www.googleapis.com/drive/v3/files?" + q, access_token)
    files = data.get("files") if isinstance(data, dict) else []
    return str(files[0].get("id") or "") if files else ""


def drive_load_identity(access_token: str) -> dict:
    if not access_token:
        return {}
    try:
        fid = drive_find_identity(access_token)
        if not fid:
            return {}
        req = urllib.request.Request(
            "https://www.googleapis.com/drive/v3/files/" + urllib.parse.quote(fid) + "?alt=media",
            headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
        if int(data.get("version") or 0) != 1:
            return {}
        ok, _, pseudo = validate_pseudo(data.get("pseudo")) if data.get("pseudo") else (True, "", "")
        aid = str(data.get("account_id") or "")
        if pseudo and not ok:
            return {}
        if aid and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", aid):
            return {}
        return {
            "account_id": aid,
            "pseudo": pseudo,
            "elo": max(0, int(data.get("elo") or 100)),
            "ranked_matches": max(0, int(data.get("ranked_matches") or 0)),
            "wins": max(0, int(data.get("wins") or 0)),
            "losses": max(0, int(data.get("losses") or 0)),
            "best_elo": max(0, int(data.get("best_elo") or data.get("elo") or 100)),
        }
    except Exception as exc:
        print("[auth] Drive appData lecture ignorée:", exc)
        return {}


def drive_save_identity(access_token: str, account: dict, profile: dict | None = None) -> bool:
    if not access_token or not account or not account.get("account_id") or not account.get("pseudo"):
        return False
    try:
        profile = dict(profile or {})
        payload = {
            "version": 1,
            "account_id": str(account.get("account_id") or ""),
            "pseudo": str(account.get("pseudo") or ""),
            "elo": max(0, int(account.get("elo") or 100)),
            "ranked_matches": max(0, int(profile.get("ranked_matches") or 0)),
            "wins": max(0, int(profile.get("wins") or 0)),
            "losses": max(0, int(profile.get("losses") or 0)),
            "best_elo": max(0, int(profile.get("best_elo") or account.get("elo") or 100)),
            "updated_at": now(),
        }
        fid = drive_find_identity(access_token)
        if not fid:
            meta = json_bytes({"name": DRIVE_FILE_NAME, "parents": ["appDataFolder"]})
            created = _google_json("https://www.googleapis.com/drive/v3/files?fields=id", access_token, method="POST", body=meta)
            fid = str(created.get("id") or "")
        if not fid:
            return False
        raw = json_bytes(payload)
        _google_json(
            "https://www.googleapis.com/upload/drive/v3/files/" + urllib.parse.quote(fid) + "?uploadType=media",
            access_token,
            method="PATCH",
            body=raw,
            content_type="application/json; charset=utf-8",
        )
        return True
    except Exception as exc:
        print("[auth] Drive appData sauvegarde ignorée:", exc)
        return False


def verify_legacy_mqtt(pseudo: str, account_id: str, token: str, timeout=5.0) -> bool:
    try:
        import paho.mqtt.client as mqtt
    except Exception:
        raise RuntimeError("Le serveur doit installer paho-mqtt pour migrer les anciens comptes.")
    key = hashlib.sha256(normalize_pseudo(pseudo).encode()).hexdigest()
    topic = f"{IDENTITY_NAMESPACE}/claim/{key}"
    got = []
    ev = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            sig = str(payload.get("sig") or "")
            body = dict(payload); body.pop("sig", None)
            raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            expected = hmac.new(IDENTITY_APP_KEY, raw, hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected):
                got.append(payload); ev.set()
        except Exception:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="yugito-auth-" + secrets.token_hex(6))
    client.tls_set(); client.on_connect = on_connect; client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 20); client.loop_start()
    try:
        ev.wait(timeout)
    finally:
        client.loop_stop(); client.disconnect()
    if not got:
        return False
    p = got[-1]
    return (
        str(p.get("account_id") or "") == str(account_id)
        and hmac.compare_digest(str(p.get("token_fp") or ""), hashlib.sha256(token.encode()).hexdigest())
        and normalize_pseudo(str(p.get("pseudo") or "")) == normalize_pseudo(pseudo)
    )


def _session_google_access(ctx) -> str:
    sess = dict(ctx.get("session") or {}) if ctx else {}
    if int(sess.get("google_expires_at") or 0) and int(sess.get("google_expires_at") or 0) <= now():
        return ""
    return str(sess.get("google_access_token") or "")


def _restore_or_create_account(conn, claims: dict, access_token: str):
    sub = str(claims.get("sub") or "")
    row = conn.execute("SELECT * FROM accounts WHERE google_sub=?", (sub,)).fetchone()
    drive_identity = drive_load_identity(access_token)
    drive_aid = str(drive_identity.get("account_id") or "")
    drive_pseudo = str(drive_identity.get("pseudo") or "")
    drive_elo = max(0, int(drive_identity.get("elo") or 100))

    if not row:
        aid = drive_aid or secrets.token_hex(16)
        t = now()
        pseudo = drive_pseudo or None
        pn = normalize_pseudo(pseudo) if pseudo else None
        # Si un account_id/pseudo identique existe dans une vieille ligne locale,
        # il doit appartenir au même Google sub pour être repris.
        clash_id = conn.execute("SELECT google_sub FROM accounts WHERE account_id=?", (aid,)).fetchone()
        if clash_id and str(clash_id["google_sub"]) != sub:
            aid = secrets.token_hex(16)
        if pn:
            clash_p = conn.execute("SELECT google_sub FROM accounts WHERE pseudo_norm=?", (pn,)).fetchone()
            if clash_p and str(clash_p["google_sub"]) != sub:
                pseudo = None; pn = None
        conn.execute(
            "INSERT INTO accounts(account_id,google_sub,email,display_name,picture,pseudo,pseudo_norm,elo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (aid, sub, claims.get("email", ""), claims.get("name", ""), claims.get("picture", ""), pseudo, pn, drive_elo, t, t),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE google_sub=?", (sub,)).fetchone()
    else:
        current_aid = str(row["account_id"])
        # Drive est la source persistante du lien Google -> account_id/pseudo.
        if drive_aid and drive_aid != current_aid:
            clash = conn.execute("SELECT google_sub FROM accounts WHERE account_id=?", (drive_aid,)).fetchone()
            if not clash or str(clash["google_sub"]) == sub:
                conn.execute("UPDATE accounts SET account_id=? WHERE account_id=?", (drive_aid, current_aid))
                conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?", (drive_aid, current_aid))
                conn.execute("UPDATE devices SET account_id=? WHERE account_id=?", (drive_aid, current_aid))
        conn.execute(
            "UPDATE accounts SET email=?,display_name=?,picture=?,updated_at=? WHERE google_sub=?",
            (claims.get("email", ""), claims.get("name", ""), claims.get("picture", ""), now(), sub),
        )
        if drive_pseudo:
            clash = conn.execute("SELECT google_sub FROM accounts WHERE pseudo_norm=? AND google_sub<>?", (normalize_pseudo(drive_pseudo), sub)).fetchone()
            if not clash:
                conn.execute("UPDATE accounts SET pseudo=?,pseudo_norm=?,elo=MAX(elo,?),updated_at=? WHERE google_sub=?", (drive_pseudo, normalize_pseudo(drive_pseudo), drive_elo, now(), sub))
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE google_sub=?", (sub,)).fetchone()
    return row, drive_identity


class Handler(BaseHTTPRequestHandler):
    server_version = "YUGITOAuth/1.1.8"

    def log_message(self, fmt, *args):
        print("[auth]", fmt % args)

    def _json(self, code, obj):
        raw = json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def _html(self, code, body):
        raw = (
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>body{font-family:system-ui;background:#07111e;color:white;display:grid;place-items:center;height:100vh;margin:0}.c{max-width:620px;padding:36px;border:1px solid #b56d2f;border-radius:20px;background:#0e1b2d;text-align:center}h1{color:#f3a64c}</style>"
            f"<div class=c>{body}</div>"
        ).encode()
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("Payload trop grand")
        return json.loads(self.rfile.read(n).decode() or "{}")

    def do_OPTIONS(self):
        self._json(200, {"ok": True})

    def do_POST(self):
        try:
            if self.path == "/api/device/start":
                b = self._body(); code = secrets.token_urlsafe(32)
                conn = db(); conn.execute(
                    "INSERT INTO devices(device_code,platform,created_at,expires_at) VALUES(?,?,?,?)",
                    (code, str(b.get("platform") or "unknown")[:20], now(), now() + DEVICE_TTL),
                ); conn.commit(); conn.close()
                self._json(200, {
                    "ok": True,
                    "device_code": code,
                    "verification_url": PUBLIC_BASE + "/login?device_code=" + urllib.parse.quote(code),
                    "expires_in": DEVICE_TTL,
                    "interval": 2,
                }); return

            if self.path == "/api/account/claim-pseudo":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                acc = dict(ctx["account"])
                b = self._body(); ok, msg, pseudo = validate_pseudo(b.get("pseudo"))
                if not ok:
                    self._json(400, {"ok": False, "error": msg}); return
                legacy = b.get("legacy") if isinstance(b.get("legacy"), dict) else None
                recovery = b.get("recovery") if isinstance(b.get("recovery"), dict) else None
                conn = db()
                clash = conn.execute("SELECT * FROM accounts WHERE pseudo_norm=? AND google_sub<>?", (normalize_pseudo(pseudo), acc["google_sub"])).fetchone()
                if clash:
                    conn.close(); self._json(409, {"ok": False, "error": "Ce pseudo est déjà lié à un autre compte Google."}); return

                migrated_account_id = None
                if legacy:
                    lp = str(legacy.get("pseudo") or ""); laid = str(legacy.get("account_id") or ""); ltok = str(legacy.get("token") or "")
                    if normalize_pseudo(lp) == normalize_pseudo(pseudo) and laid and ltok:
                        if not verify_legacy_mqtt(lp, laid, ltok):
                            conn.close(); self._json(403, {"ok": False, "error": "Ce pseudo existe déjà, mais l'ancien compte local n'a pas pu être vérifié."}); return
                        used = conn.execute("SELECT google_sub FROM accounts WHERE account_id=? AND google_sub<>?", (laid, acc["google_sub"])).fetchone()
                        if used:
                            conn.close(); self._json(409, {"ok": False, "error": "Cet ancien compte YUGITO est déjà lié à un autre compte Google."}); return
                        old_id = acc["account_id"]
                        if laid != old_id:
                            conn.execute("UPDATE accounts SET account_id=?,legacy_account_id=? WHERE account_id=?", (laid, laid, old_id))
                            conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?", (laid, old_id))
                            conn.execute("UPDATE devices SET account_id=? WHERE account_id=?", (laid, old_id))
                            migrated_account_id = laid
                        else:
                            conn.execute("UPDATE accounts SET legacy_account_id=? WHERE account_id=?", (laid, laid))

                # 1.1.8 : bootstrap silencieux après la dernière base SQLite
                # éphémère. Le client peut fournir son dernier pseudo connu. On ne
                # l'utilise que si l'e-mail Google correspond exactement et si le
                # pseudo est libre. Une fois écrit dans Drive appData, ce secours
                # n'est plus nécessaire lors des connexions suivantes.
                aid = migrated_account_id or acc["account_id"]
                if recovery and not str(acc.get("pseudo") or ""):
                    rp = str(recovery.get("pseudo") or "")
                    reml = normalize_email(recovery.get("email"))
                    if normalize_pseudo(rp) == normalize_pseudo(pseudo) and reml and reml == normalize_email(acc.get("email")):
                        old_aid = str(recovery.get("account_id") or "")
                        if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", old_aid):
                            used = conn.execute("SELECT google_sub FROM accounts WHERE account_id=? AND google_sub<>?", (old_aid, acc["google_sub"])).fetchone()
                            if not used and old_aid != aid:
                                conn.execute("UPDATE accounts SET account_id=? WHERE account_id=?", (old_aid, aid))
                                conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?", (old_aid, aid))
                                conn.execute("UPDATE devices SET account_id=? WHERE account_id=?", (old_aid, aid))
                                aid = old_aid

                conn.execute("UPDATE accounts SET pseudo=?,pseudo_norm=?,updated_at=? WHERE account_id=?", (pseudo, normalize_pseudo(pseudo), now(), aid))
                conn.commit(); row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (aid,)).fetchone()
                access = _session_google_access(ctx)
                if access:
                    drive_save_identity(access, dict(row))
                new_token = issue_session(conn, row, access, int((ctx.get("session") or {}).get("google_expires_at") or 0))
                conn.close()
                self._json(200, {"ok": True, "account": account_public(row), "session_token": new_token, "migrated_legacy": bool(migrated_account_id), "auto_recovered": bool(recovery)}); return

            if self.path == "/api/account/sync-elo":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                b = self._body(); elo = max(0, int(b.get("elo") or 100)); profile = b.get("profile") if isinstance(b.get("profile"), dict) else {}
                conn = db(); conn.execute("UPDATE accounts SET elo=?,updated_at=? WHERE account_id=?", (elo, now(), ctx["account"]["account_id"])); conn.commit()
                row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (ctx["account"]["account_id"],)).fetchone()
                access = _session_google_access(ctx)
                if access:
                    drive_save_identity(access, dict(row), profile)
                new_token = issue_session(conn, row, access, int((ctx.get("session") or {}).get("google_expires_at") or 0))
                conn.close(); self._json(200, {"ok": True, "account": account_public(row), "session_token": new_token}); return

            if self.path == "/api/account/link-legacy":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                acc = ctx["account"]; b = self._body(); pseudo = str(b.get("pseudo") or ""); aid = str(b.get("account_id") or ""); tok = str(b.get("token") or "")
                if not verify_legacy_mqtt(pseudo, aid, tok):
                    self._json(403, {"ok": False, "error": "Impossible de vérifier l'ancien compte YUGITO."}); return
                conn = db(); clash = conn.execute("SELECT google_sub FROM accounts WHERE pseudo_norm=? AND google_sub<>?", (normalize_pseudo(pseudo), acc["google_sub"])).fetchone()
                if clash:
                    conn.close(); self._json(409, {"ok": False, "error": "Ce pseudo est déjà lié à un autre compte Google."}); return
                old_id = acc["account_id"]
                used = conn.execute("SELECT google_sub FROM accounts WHERE account_id=? AND google_sub<>?", (aid, acc["google_sub"])).fetchone()
                if used:
                    conn.close(); self._json(409, {"ok": False, "error": "Cet ancien account_id est déjà utilisé."}); return
                if aid and aid != old_id:
                    conn.execute("UPDATE accounts SET account_id=? WHERE account_id=?", (aid, old_id)); conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?", (aid, old_id)); conn.execute("UPDATE devices SET account_id=? WHERE account_id=?", (aid, old_id))
                target = aid or old_id
                conn.execute("UPDATE accounts SET legacy_account_id=?,pseudo=?,pseudo_norm=?,updated_at=? WHERE account_id=?", (aid or None, pseudo, normalize_pseudo(pseudo), now(), target)); conn.commit()
                row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (target,)).fetchone(); access = _session_google_access(ctx)
                if access: drive_save_identity(access, dict(row))
                new_token = issue_session(conn, row, access, int((ctx.get("session") or {}).get("google_expires_at") or 0)); conn.close()
                self._json(200, {"ok": True, "account": account_public(row), "session_token": new_token}); return

            if self.path == "/api/logout":
                hdr = str(self.headers.get("Authorization") or "")
                if hdr.startswith("Bearer "):
                    conn = db(); conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(hdr[7:].strip()),)); conn.commit(); conn.close()
                self._json(200, {"ok": True}); return

            self._json(404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})

    def do_GET(self):
        try:
            u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
            if u.path == "/health":
                self._json(200, {"ok": True, "service": "YUGITO Auth", "version": "1.1.8", "drive_identity": True, "stateless_sessions": True, "time": now()}); return

            if u.path == "/oauth/start":
                dc = secrets.token_urlsafe(32); conn = db(); conn.execute("INSERT INTO devices(device_code,platform,created_at,expires_at) VALUES(?,?,?,?)", (dc, "browser-test", now(), now() + DEVICE_TTL)); conn.commit(); conn.close()
                self.send_response(302); self.send_header("Location", PUBLIC_BASE + "/login?device_code=" + urllib.parse.quote(dc)); self.send_header("Cache-Control", "no-store"); self.end_headers(); return

            if u.path == "/login":
                dc = (q.get("device_code") or [""])[0]
                conn = db(); row = conn.execute("SELECT * FROM devices WHERE device_code=? AND expires_at>?", (dc, now())).fetchone(); conn.close()
                if not row:
                    self._html(400, "<h1>Lien expiré</h1><p>Relance la connexion depuis YUGITO.</p>"); return
                params = {
                    "client_id": GOOGLE_CLIENT_ID,
                    "redirect_uri": PUBLIC_BASE + "/oauth/callback",
                    "response_type": "code",
                    "scope": "openid email profile " + DRIVE_SCOPE,
                    "state": dc,
                    "prompt": "select_account",
                    "include_granted_scopes": "true",
                }
                self.send_response(302); self.send_header("Location", "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)); self.send_header("Cache-Control", "no-store"); self.end_headers(); return

            if u.path == "/oauth/callback":
                code = (q.get("code") or [""])[0]; state = (q.get("state") or [""])[0]
                conn = db(); dev = conn.execute("SELECT * FROM devices WHERE device_code=? AND expires_at>?", (state, now())).fetchone()
                if not dev:
                    conn.close(); self._html(400, "<h1>Connexion expirée</h1>"); return
                claims, tok = google_exchange(code); sub = str(claims.get("sub") or "")
                access = str(tok.get("access_token") or ""); expires = now() + max(0, int(tok.get("expires_in") or 3600) - 30)
                row, drive_identity = _restore_or_create_account(conn, claims, access)
                # Si la DB avait déjà le pseudo mais pas encore le fichier Google,
                # on crée automatiquement la sauvegarde canonique.
                if row and row["pseudo"] and not drive_identity:
                    drive_save_identity(access, dict(row))
                session = issue_session(conn, row, access, expires)
                conn.execute("UPDATE devices SET account_id=?,session_token=? WHERE device_code=?", (row["account_id"], session, state)); conn.commit(); conn.close()
                self._html(200, "<h1>Compte Google lié ✅</h1><p>YUGITO a récupéré ton identité. Tu peux revenir au jeu.</p><script>setTimeout(()=>{try{window.close()}catch(e){}},500)</script>"); return

            if u.path == "/api/device/status":
                dc = (q.get("device_code") or [""])[0]; conn = db(); dev = conn.execute("SELECT * FROM devices WHERE device_code=? AND expires_at>?", (dc, now())).fetchone()
                if not dev:
                    conn.close(); self._json(410, {"ok": False, "status": "expired"}); return
                if not dev["account_id"]:
                    conn.close(); self._json(200, {"ok": True, "status": "pending"}); return
                row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (dev["account_id"],)).fetchone(); token = str(dev["session_token"] or "")
                conn.close(); self._json(200, {"ok": True, "status": "authenticated", "session_token": token, "account": account_public(row)}); return

            if u.path == "/api/account/me":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                self._json(200, {"ok": True, "account": account_public(ctx["account"])}); return

            self._json(404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._html(500, "<h1>Erreur YUGITO</h1><p>" + str(exc).replace("<", "&lt;") + "</p>")


def main():
    missing = []
    if not PUBLIC_BASE: missing.append("YUGITO_PUBLIC_BASE_URL (ou RENDER_EXTERNAL_URL)")
    if not GOOGLE_CLIENT_ID: missing.append("GOOGLE_CLIENT_ID")
    if not GOOGLE_CLIENT_SECRET: missing.append("GOOGLE_CLIENT_SECRET")
    if missing:
        print("ERREUR: variables d'environnement manquantes : " + ", ".join(missing)); raise SystemExit(2)
    db().close()
    print(f"YUGITO Auth 1.1.8 sur 0.0.0.0:{PORT} -> {PUBLIC_BASE}")
    print("Identite persistante: Google Drive appData + sessions signees")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
