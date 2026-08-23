# YUGITO Auth Server 1.4.10 — Native Google + Drive appData + sessions signees
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

import yugito_economy as economy

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
SERVER_PATCH_VERSION = "1.5.2-social-profile-sync-1"


def server_diag(stage: str, message: str = ""):
    try:
        print(f"[YUGITO-AUTH] stage={stage} | {message}", flush=True)
    except Exception:
        pass


def now() -> int:
    return int(time.time())


def normalize_pseudo(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    return " ".join(value.split()).casefold()


def normalize_email(value: str) -> str:
    return str(value or "").strip().casefold()


def dev_account_authorized(account) -> bool:
    return economy.dev_account_authorized(dict(account or {}))


def economy_state_for_ctx(ctx, include_catalog: bool=True) -> dict:
    st=economy.state(str(ctx["account"]["account_id"]), include_catalog)
    st["dev_mode_available"]=bool(dev_account_authorized(ctx.get("account")))
    st["elo"]=int(ctx["account"]["elo"] or 100)
    return st


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


def verify_google_id_token(id_token: str):
    """Vérifie l'ID token Android sans jamais le journaliser."""
    id_token = str(id_token or "").strip()
    if not id_token or len(id_token) > 20000:
        raise RuntimeError("Jeton Google invalide.")
    with urllib.request.urlopen(
        "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(id_token), timeout=15
    ) as r:
        claims = json.loads(r.read().decode())
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        raise RuntimeError("Audience Google invalide.")
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise RuntimeError("Émetteur Google invalide.")
    if int(claims.get("exp") or 0) <= now():
        raise RuntimeError("Token Google expiré.")
    if str(claims.get("email_verified", claims.get("verified_email", "true"))).lower() not in ("true", "1"):
        raise RuntimeError("Adresse Google non vérifiée.")
    return claims


def validate_drive_access_token(access_token: str, claims: dict) -> int:
    """Valide l'accès Drive appData et, si Google renvoie l'e-mail Drive, vérifie qu'il correspond à l'ID token."""
    access_token = str(access_token or "").strip()
    if not access_token or len(access_token) > 20000:
        return 0
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress,me)",
        headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            about = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError("Accès Google Drive appData refusé ou invalide." + (" (HTTP %s)" % exc.code))
    user = about.get("user") if isinstance(about, dict) else {}
    drive_email = normalize_email((user or {}).get("emailAddress"))
    id_email = normalize_email(claims.get("email"))
    if drive_email and id_email and drive_email != id_email:
        raise RuntimeError("Le jeton Drive appartient à un autre compte Google.")
    return now() + 3300


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
    server_version = "YUGITOAuth/1.4.10"

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
            if self.path == "/api/dev/grant-yt":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not dev_account_authorized(ctx["account"]):
                    self._json(403, {"ok": False, "error": "Compte DEV non autorisé."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                try:
                    b=self._body(); data=economy.dev_grant_yt(str(ctx["account"]["account_id"]), int(b.get("amount") or 0))
                    data["state"]=economy_state_for_ctx(ctx, False)
                    self._json(200,data); return
                except ValueError as exc:
                    self._json(400,{"ok":False,"error":str(exc)}); return

            if self.path == "/api/dev/grant-elo":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not dev_account_authorized(ctx["account"]):
                    self._json(403, {"ok": False, "error": "Compte DEV non autorisé."}); return
                try:
                    b=self._body(); amount=int(b.get("amount") or 0)
                    if amount <= 0: raise ValueError("Le montant ELO doit être positif.")
                    if amount > 1_000_000: raise ValueError("Maximum 1 000 000 ELO par opération DEV.")
                    aid=str(ctx["account"]["account_id"]); conn=db()
                    row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(aid,)).fetchone()
                    before=int(row["elo"] or 100); after=before+amount
                    conn.execute("UPDATE accounts SET elo=?,updated_at=? WHERE account_id=?",(after,now(),aid)); conn.commit()
                    row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(aid,)).fetchone()
                    access=_session_google_access(ctx)
                    if access: drive_save_identity(access,dict(row))
                    conn.close(); economy.dev_record_elo(aid,amount,before,after)
                    ctx2={"account":dict(row)}
                    st=economy.state(aid,False); st["dev_mode_available"]=True; st["elo"]=after
                    self._json(200,{"ok":True,"amount":amount,"elo":after,"account":account_public(row),"state":st}); return
                except ValueError as exc:
                    self._json(400,{"ok":False,"error":str(exc)}); return

            if self.path == "/api/dev/remove-card":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not dev_account_authorized(ctx["account"]):
                    self._json(403, {"ok": False, "error": "Compte DEV non autorisé."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                try:
                    b=self._body(); data=economy.dev_remove_card(str(ctx["account"]["account_id"]),str(b.get("card_id") or ""))
                    data["state"]=economy_state_for_ctx(ctx,False)
                    self._json(200,data); return
                except ValueError as exc:
                    self._json(400,{"ok":False,"error":str(exc)}); return

            if self.path == "/api/economy/purchase":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                try:
                    b = self._body()
                    self._json(200, economy.purchase(str(ctx["account"]["account_id"]), str(b.get("card_id") or ""))); return
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)}); return

            if self.path == "/api/economy/validate-deck":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                b = self._body()
                result = economy.validate_deck(str(ctx["account"]["account_id"]), list(b.get("card_ids") or []), bool(b.get("require_eight", True)))
                self._json(200 if result.get("ok") else 400, result); return

            if self.path == "/api/economy/match-permit":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                try:
                    b = self._body()
                    self._json(200, economy.issue_permit(str(ctx["account"]["account_id"]), str(b.get("mode") or "classic"))); return
                except ValueError as exc:
                    self._json(403, {"ok": False, "error": str(exc)}); return

            if self.path == "/api/economy/verify-permit":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                b = self._body(); payload = economy.verify_permit(str(b.get("permit") or ""))
                if not payload:
                    self._json(400, {"ok": False, "error": "Permis multijoueur invalide ou expiré."}); return
                self._json(200, {"ok": True, "permit": payload}); return

            if self.path == "/api/economy/settle-match":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Économie YUGITO indisponible."}); return
                try:
                    self._json(200, economy.settle_match(str(ctx["account"]["account_id"]), self._body())); return
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)}); return

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

            if self.path == "/api/google/native":
                server_diag("google.native.begin", "POST /api/google/native reçu; ID token et access token non journalisés")
                b = self._body()
                try:
                    claims = verify_google_id_token(str(b.get("id_token") or ""))
                except Exception as exc:
                    server_diag("google.native.reject", str(exc))
                    self._json(401, {"ok": False, "error": str(exc)}); return
                access = str(b.get("drive_access_token") or "").strip()
                access_expires = 0
                drive_access = False
                if access:
                    try:
                        access_expires = validate_drive_access_token(access, claims)
                        drive_access = True
                        server_diag("drive.native.ok", "Jeton Drive appData validé pour le compte Google sélectionné.")
                    except Exception as exc:
                        server_diag("drive.native.reject", str(exc))
                        access = ""; access_expires = 0
                else:
                    server_diag("drive.native.missing", "Aucun access token Drive fourni; connexion possible mais restauration appData indisponible.")
                conn = db()
                try:
                    row, drive_identity = _restore_or_create_account(conn, claims, access)
                    if row and row["pseudo"] and access and not drive_identity:
                        drive_save_identity(access, dict(row))
                    session = issue_session(conn, row, access, access_expires)
                    conn.commit()
                    public = account_public(row)
                finally:
                    conn.close()
                restored = bool(drive_identity and drive_identity.get("pseudo"))
                server_diag("google.native.ok", "Compte YUGITO résolu; pseudo=%s; drive_access=%s; drive_restored=%s" % ("oui" if public and public.get("pseudo") else "non", drive_access, restored))
                try:
                    economy.record_event(str(public.get("account_id") or ""), "login_google_native", details={"drive_identity_restored": restored})
                except Exception as exc:
                    server_diag("economy.audit.login.error", str(exc))
                self._json(200, {"ok": True, "session_token": session, "account": public, "drive_access": drive_access, "drive_identity_restored": restored}); return

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
                remote_profile = economy.profile_sync(ctx["account"]["account_id"], profile, elo) if economy.available() else profile
                conn.close(); self._json(200, {"ok": True, "account": account_public(row), "session_token": new_token, "profile": remote_profile}); return

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

            if self.path == "/api/social/sync":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Stockage social indisponible."}); return
                b=self._body(); friends=b.get("friends") if isinstance(b.get("friends"),list) else []
                self._json(200, economy.social_merge(ctx["account"]["account_id"], friends)); return

            if self.path == "/api/social/set":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Stockage social indisponible."}); return
                b=self._body()
                self._json(200, economy.social_set(ctx["account"]["account_id"], str(b.get("friend_account_id") or ""), str(b.get("pseudo") or ""), bool(b.get("active",True)))); return

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
                payload = {"ok": True, "service": "YUGITO Auth", "version": "1.5.2", "server_patch": SERVER_PATCH_VERSION, "native_google": True, "native_google_route": "/api/google/native", "drive_identity": True, "drive_scope": DRIVE_SCOPE, "stateless_sessions": True, "time": now()}
                payload.update(economy.health()); payload.update({"social_sync": bool(economy.available()), "profile_sync": bool(economy.available())})
                self._json(200, payload); return

            if u.path == "/api/economy/state":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "economy_available": False, "error": "Économie YUGITO indisponible."}); return
                self._json(200, economy_state_for_ctx(ctx, True)); return

            if u.path == "/api/social/friends":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                if not economy.available():
                    self._json(503, {"ok": False, "error": "Stockage social indisponible."}); return
                self._json(200, economy.social_merge(ctx["account"]["account_id"], [])); return

            if u.path == "/api/account/profile":
                ctx = auth_context(self.headers)
                if not ctx:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                prof=economy.profile_state(ctx["account"]["account_id"], int(ctx["account"]["elo"] or 100)) if economy.available() else {}
                self._json(200, {"ok":True,"profile":prof}); return

            if u.path == "/api/admin/account-logs":
                if not economy.admin_authorized(self.headers):
                    self._json(403, {"ok": False, "error": "Accès administrateur refusé."}); return
                aid = str((q.get("account_id") or [""])[0])
                if not aid:
                    self._json(400, {"ok": False, "error": "account_id requis."}); return
                limit = int((q.get("limit") or ["200"])[0] or 200)
                self._json(200, economy.account_logs(aid, limit)); return

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
                try:
                    economy.record_event(str(row["account_id"]), "login_google_browser")
                except Exception as exc:
                    server_diag("economy.audit.login.error", str(exc))
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
    try:
        economy.init_schema()
        server_diag("economy.schema", "PostgreSQL prêt" if economy.available() else "non configuré - Solo client restera disponible")
    except Exception as exc:
        server_diag("economy.schema.error", str(exc))
    print(f"YUGITO Auth 1.5.1 sur 0.0.0.0:{PORT} -> {PUBLIC_BASE}")
    server_diag("server.start", f"patch={SERVER_PATCH_VERSION}; native_google=true; drive_identity=true; stateless_sessions=true; economy={economy.available()}; public_base={PUBLIC_BASE}")
    print("Identite persistante: Google Drive appData + sessions signees")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
