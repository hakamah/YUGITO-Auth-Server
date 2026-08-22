from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.getenv("YUGITO_AUTH_DB", "yugito_auth.sqlite3")
PUBLIC_BASE = os.getenv("YUGITO_PUBLIC_BASE_URL", "").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
PORT = int(os.getenv("PORT", "8787"))
SESSION_TTL = 60 * 60 * 24 * 30
DEVICE_TTL = 10 * 60
IDENTITY_NAMESPACE = "yugito/v40-21/identity"
IDENTITY_APP_KEY = b"YUGITO-V40-21-IDENTITY-REGISTRY-2026"
MQTT_HOST = os.getenv("YUGITO_MQTT_HOST", "broker.emqx.io")
MQTT_PORT = int(os.getenv("YUGITO_MQTT_PORT", "8883"))
SERVER_PATCH_VERSION = "1.4.9-native-google-1"


def server_diag(stage: str, message: str = ""):
    """Diagnostic serveur sans journaliser token, email ou identifiant Google."""
    try:
        print(f"[YUGITO-AUTH] stage={stage} | {message}", flush=True)
    except Exception:
        pass


def now() -> int:
    return int(time.time())


def normalize_pseudo(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    return " ".join(value.split()).casefold()


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
    c.executescript("""
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
      expires_at INTEGER NOT NULL
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
    """)
    # Migrations compatibles avec une base déjà créée.
    cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)").fetchall()}
    if "elo" not in cols:
        c.execute("ALTER TABLE accounts ADD COLUMN elo INTEGER NOT NULL DEFAULT 100")
        c.commit()
    return c


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session(conn, account_id: str) -> str:
    token = secrets.token_urlsafe(48)
    conn.execute("INSERT INTO sessions(token_hash, account_id, created_at, expires_at) VALUES(?,?,?,?)",
                 (token_hash(token), account_id, now(), now() + SESSION_TTL))
    return token


def auth_account(headers):
    hdr = str(headers.get("Authorization") or "")
    if not hdr.startswith("Bearer "):
        return None
    token = hdr[7:].strip()
    if not token:
        return None
    conn = db()
    row = conn.execute("SELECT account_id FROM sessions WHERE token_hash=? AND expires_at>?", (token_hash(token), now())).fetchone()
    if not row:
        conn.close(); return None
    acc = conn.execute("SELECT * FROM accounts WHERE account_id=?", (row["account_id"],)).fetchone()
    conn.close()
    return dict(acc) if acc else None


def account_public(row):
    if not row:
        return None
    return {
        "account_id": row["account_id"],
        "pseudo": row["pseudo"] or "",
        "email": row["email"] or "",
        "name": row["display_name"] or "",
        "picture": row["picture"] or "",
        "google_linked": True,
        "elo": int(row["elo"] if "elo" in row.keys() else 100),
    }


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
    return claims


def verify_google_id_token(id_token: str):
    """Vérifie un ID token Google reçu directement depuis Android.

    Important: le token lui-même n'est jamais écrit dans les logs.
    """
    id_token = str(id_token or "").strip()
    if not id_token or len(id_token) > 20000:
        raise RuntimeError("Jeton Google invalide.")
    with urllib.request.urlopen(
        "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(id_token),
        timeout=15,
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


def verify_legacy_mqtt(pseudo: str, account_id: str, token: str, timeout=5.0) -> bool:
    """Vérifie l'ancien compte YUGITO retained MQTT avant migration Google."""
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
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 20)
    client.loop_start()
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


class Handler(BaseHTTPRequestHandler):
    server_version = "YUGITOAuth/1.0"
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
        raw = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
               "<style>body{font-family:system-ui;background:#07111e;color:white;display:grid;place-items:center;height:100vh;margin:0}.c{max-width:620px;padding:36px;border:1px solid #b56d2f;border-radius:20px;background:#0e1b2d;text-align:center}h1{color:#f3a64c}</style>"
               f"<div class=c>{body}</div>").encode()
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000: raise ValueError("Payload trop grand")
        return json.loads(self.rfile.read(n).decode() or "{}")

    def do_OPTIONS(self):
        self._json(200, {"ok": True})

    def do_POST(self):
        try:
            if self.path == "/api/device/start":
                b = self._body(); code = secrets.token_urlsafe(32)
                conn = db(); conn.execute("INSERT INTO devices(device_code,platform,created_at,expires_at) VALUES(?,?,?,?)", (code, str(b.get("platform") or "unknown")[:20], now(), now()+DEVICE_TTL)); conn.commit(); conn.close()
                self._json(200, {"ok": True, "device_code": code, "verification_url": PUBLIC_BASE + "/login?device_code=" + urllib.parse.quote(code), "expires_in": DEVICE_TTL, "interval": 2}); return
            if self.path == "/api/google/native":
                server_diag("google.native.begin", "POST /api/google/native reçu; token non journalisé")
                b = self._body()
                try:
                    claims = verify_google_id_token(str(b.get("id_token") or ""))
                except Exception as e:
                    server_diag("google.native.reject", f"{type(e).__name__}: {e}")
                    self._json(401,{"ok":False,"error":str(e)}); return
                sub = str(claims.get("sub") or claims.get("user_id") or "")
                if not sub:
                    server_diag("google.native.reject", "Identité Google sans sub/user_id")
                    self._json(400,{"ok":False,"error":"Identité Google incomplète."}); return
                conn=db(); row=conn.execute("SELECT * FROM accounts WHERE google_sub=?",(sub,)).fetchone()
                created = False
                if not row:
                    aid=secrets.token_hex(16); t=now(); created = True
                    conn.execute("INSERT INTO accounts(account_id,google_sub,email,display_name,picture,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(aid,sub,claims.get("email","") ,claims.get("name","") ,claims.get("picture","") ,t,t)); conn.commit(); row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(aid,)).fetchone()
                else:
                    conn.execute("UPDATE accounts SET email=?,display_name=?,picture=?,updated_at=? WHERE account_id=?",(claims.get("email",row["email"] or ""),claims.get("name",row["display_name"] or ""),claims.get("picture",row["picture"] or ""),now(),row["account_id"])); conn.commit(); row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(row["account_id"],)).fetchone()
                session=new_session(conn,row["account_id"]); conn.commit(); conn.close()
                server_diag("google.native.ok", f"Compte {'créé' if created else 'retrouvé'}; pseudo_present={bool(row['pseudo'])}")
                self._json(200,{"ok":True,"session_token":session,"account":account_public(row)}); return
            if self.path == "/api/account/claim-pseudo":
                # Flux officiel : Google d'abord, puis choix du pseudo.
                # Si le pseudo appartient à l'ancienne identité locale du PC,
                # le client peut joindre une preuve legacy : la migration se fait
                # alors DANS cette même étape, sans écran de liaison séparé.
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401,{"ok":False,"error":"Session invalide."}); return
                b=self._body(); ok,msg,pseudo=validate_pseudo(b.get("pseudo"))
                if not ok:
                    self._json(400,{"ok":False,"error":msg}); return
                legacy = b.get("legacy") if isinstance(b.get("legacy"), dict) else None
                conn=db()
                clash=conn.execute("SELECT * FROM accounts WHERE pseudo_norm=? AND account_id<>?",(normalize_pseudo(pseudo),acc["account_id"])).fetchone()
                if clash:
                    conn.close(); self._json(409,{"ok":False,"error":"Ce pseudo est déjà lié à un autre compte Google."}); return

                # Le registre historique MQTT peut déjà réserver ce pseudo sans
                # qu'il existe dans la nouvelle base Google. Sur PC, si l'utilisateur
                # saisit exactement SON ancien pseudo, le launcher envoie sa preuve
                # locale et on adopte l'ancien account_id afin de conserver l'identité.
                migrated_account_id = None
                if legacy:
                    lp=str(legacy.get("pseudo") or "")
                    laid=str(legacy.get("account_id") or "")
                    ltok=str(legacy.get("token") or "")
                    if normalize_pseudo(lp) == normalize_pseudo(pseudo) and laid and ltok:
                        if not verify_legacy_mqtt(lp, laid, ltok):
                            conn.close(); self._json(403,{"ok":False,"error":"Ce pseudo existe déjà, mais l'ancien compte local n'a pas pu être vérifié."}); return
                        used=conn.execute("SELECT google_sub FROM accounts WHERE account_id=? AND google_sub<>?",(laid,acc["google_sub"])).fetchone()
                        if used:
                            conn.close(); self._json(409,{"ok":False,"error":"Cet ancien compte YUGITO est déjà lié à un autre compte Google."}); return
                        old_id=acc["account_id"]
                        # account_id est l'identifiant canonique YUGITO : on reprend
                        # l'ancien pour préserver amis/ELO/données historiques.
                        if laid != old_id:
                            conn.execute("UPDATE accounts SET account_id=?,legacy_account_id=? WHERE account_id=?",(laid,laid,old_id))
                            conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?",(laid,old_id))
                            conn.execute("UPDATE devices SET account_id=? WHERE account_id=?",(laid,old_id))
                            migrated_account_id=laid
                        else:
                            conn.execute("UPDATE accounts SET legacy_account_id=? WHERE account_id=?",(laid,laid))
                aid=migrated_account_id or acc["account_id"]
                conn.execute("UPDATE accounts SET pseudo=?,pseudo_norm=?,updated_at=? WHERE account_id=?",(pseudo,normalize_pseudo(pseudo),now(),aid))
                conn.commit(); row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(aid,)).fetchone(); conn.close()
                self._json(200,{"ok":True,"account":account_public(row),"migrated_legacy":bool(migrated_account_id)}); return
            if self.path == "/api/account/link-legacy":
                acc=auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                b=self._body(); pseudo=str(b.get("pseudo") or ""); aid=str(b.get("account_id") or ""); tok=str(b.get("token") or "")
                if not verify_legacy_mqtt(pseudo, aid, tok): self._json(403,{"ok":False,"error":"Impossible de vérifier l'ancien compte YUGITO."}); return
                conn=db(); clash=conn.execute("SELECT google_sub FROM accounts WHERE pseudo_norm=? AND account_id<>?",(normalize_pseudo(pseudo),acc["account_id"])).fetchone()
                if clash: conn.close(); self._json(409,{"ok":False,"error":"Ce pseudo est déjà lié à un autre compte Google."}); return
                # conserve l'account_id historique si possible, afin de garder les profils locaux associés
                try:
                    conn.execute("UPDATE accounts SET legacy_account_id=?,pseudo=?,pseudo_norm=?,updated_at=? WHERE account_id=?",(aid,pseudo,normalize_pseudo(pseudo),now(),acc["account_id"]))
                    conn.commit()
                finally:
                    row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(acc["account_id"],)).fetchone(); conn.close()
                self._json(200,{"ok":True,"account":account_public(row)}); return
            if self.path == "/api/logout":
                hdr=str(self.headers.get("Authorization") or "")
                if hdr.startswith("Bearer "):
                    conn=db(); conn.execute("DELETE FROM sessions WHERE token_hash=?",(token_hash(hdr[7:].strip()),)); conn.commit(); conn.close()
                self._json(200,{"ok":True}); return
            self._json(404,{"ok":False,"error":"Not found"})
        except Exception as e:
            self._json(500,{"ok":False,"error":str(e)})

    def do_GET(self):
        try:
            u=urllib.parse.urlsplit(self.path); q=urllib.parse.parse_qs(u.query)
            if u.path == "/health": self._json(200,{"ok":True,"service":"YUGITO Auth","time":now(),"server_patch":SERVER_PATCH_VERSION,"native_google":True,"native_google_route":"/api/google/native"}); return
            if u.path == "/login":
                dc=(q.get("device_code") or [""])[0]
                conn=db(); row=conn.execute("SELECT * FROM devices WHERE device_code=? AND expires_at>?",(dc,now())).fetchone(); conn.close()
                if not row: self._html(400,"<h1>Lien expiré</h1><p>Relance la connexion depuis YUGITO.</p>"); return
                params={"client_id":GOOGLE_CLIENT_ID,"redirect_uri":PUBLIC_BASE+"/oauth/callback","response_type":"code","scope":"openid email profile","state":dc,"prompt":"select_account","include_granted_scopes":"true"}
                self.send_response(302); self.send_header("Location","https://accounts.google.com/o/oauth2/v2/auth?"+urllib.parse.urlencode(params)); self.end_headers(); return
            if u.path == "/oauth/callback":
                code=(q.get("code") or [""])[0]; state=(q.get("state") or [""])[0]
                conn=db(); dev=conn.execute("SELECT * FROM devices WHERE device_code=? AND expires_at>?",(state,now())).fetchone()
                if not dev: conn.close(); self._html(400,"<h1>Connexion expirée</h1>"); return
                claims=google_exchange(code); sub=str(claims.get("sub") or "")
                row=conn.execute("SELECT * FROM accounts WHERE google_sub=?",(sub,)).fetchone()
                if not row:
                    aid=secrets.token_hex(16); t=now()
                    conn.execute("INSERT INTO accounts(account_id,google_sub,email,display_name,picture,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(aid,sub,claims.get("email",""),claims.get("name",""),claims.get("picture",""),t,t)); conn.commit(); row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(aid,)).fetchone()
                else:
                    conn.execute("UPDATE accounts SET email=?,display_name=?,picture=?,updated_at=? WHERE account_id=?",(claims.get("email",""),claims.get("name",""),claims.get("picture",""),now(),row["account_id"])); conn.commit(); row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(row["account_id"],)).fetchone()
                session=new_session(conn,row["account_id"]); conn.execute("UPDATE devices SET account_id=?,session_token=? WHERE device_code=?",(row["account_id"],session,state)); conn.commit(); conn.close()
                self._html(200,"<h1>Compte Google lié ✅</h1><p>Tu peux revenir dans YUGITO. Cette fenêtre peut être fermée.</p>"); return
            if u.path == "/api/device/status":
                dc=(q.get("device_code") or [""])[0]; conn=db(); dev=conn.execute("SELECT * FROM devices WHERE device_code=? AND expires_at>?",(dc,now())).fetchone()
                if not dev: conn.close(); self._json(410,{"ok":False,"status":"expired"}); return
                if not dev["account_id"]: conn.close(); self._json(200,{"ok":True,"status":"pending"}); return
                row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(dev["account_id"],)).fetchone(); conn.close()
                self._json(200,{"ok":True,"status":"authenticated","session_token":dev["session_token"],"account":account_public(row)}); return
            if u.path == "/api/account/me":
                acc=auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                self._json(200,{"ok":True,"account":account_public(acc)}); return
            self._json(404,{"ok":False,"error":"Not found"})
        except Exception as e:
            self._html(500,"<h1>Erreur YUGITO</h1><p>"+str(e).replace("<","&lt;")+"</p>")


def main():
    if not PUBLIC_BASE or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        print("ERREUR: configure YUGITO_PUBLIC_BASE_URL, GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET")
        raise SystemExit(2)
    db().close()
    server_diag("server.start", f"patch={SERVER_PATCH_VERSION}; native_google=true; public_base={PUBLIC_BASE}")
    print(f"YUGITO Auth sur 0.0.0.0:{PORT} -> {PUBLIC_BASE}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
