# YUGITO Auth Server - 2.2.0 PRODUCTION • INSTANCES + ENTRAÎNEMENT + PERFECTION + HDV
# Start Command Render : python yugito_auth_server.py
# Variables requises : GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, DATABASE_URL (sur Render)
# YUGITO_PUBLIC_BASE_URL est facultative sur Render si RENDER_EXTERNAL_URL est disponible.
# requirements.txt : paho-mqtt>=2.1,<3 ; psycopg[binary]>=3.2,<4
#
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
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.getenv("YUGITO_AUTH_DB", "yugito_auth.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_BACKEND = "postgres" if DATABASE_URL else "sqlite"
IS_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
PUBLIC_BASE = (os.getenv("YUGITO_PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
PORT = int(os.getenv("PORT", "8787"))
SESSION_TTL = 60 * 60 * 24 * 30
DEVICE_TTL = 10 * 60
IDENTITY_NAMESPACE = "yugito/v40-21/identity"
IDENTITY_APP_KEY = b"YUGITO-V40-21-IDENTITY-REGISTRY-2026"
MQTT_HOST = os.getenv("YUGITO_MQTT_HOST", "broker.emqx.io")
MQTT_PORT = int(os.getenv("YUGITO_MQTT_PORT", "8883"))

# --- ECONOMIE YUGITO GC 2.0.10 ---
SOLO_WIN_YT = 10
SOLO_LOSS_YT = 0
SOLO_PERMIT_TTL = 4 * 60 * 60
MARKET_MIN_PRICE = 1
MARKET_MAX_PRICE = 10_000_000
MARKET_MAX_ACTIVE_LISTINGS = 30
WEEKLY_COUNTS = {"3.5": 8, "4.0": 6, "4.5": 4, "5.0": 4}
ECONOMY_CATALOG = [{'id': 'hashirama', 'name': 'Hashirama Senju', 'stars': 5.0, 'price_yt': 2000, 'purchasable': True},
 {'id': 'madara', 'name': 'Madara Uchiha', 'stars': 5.0, 'price_yt': 2000, 'purchasable': True},
 {'id': 'nagato', 'name': 'Nagato', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'obito', 'name': 'Obito Uchiha', 'stars': 5.0, 'price_yt': 2000, 'purchasable': True},
 {'id': 'itachi', 'name': 'Itachi Uchiha', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'jiraiya', 'name': 'Jiraiya', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'killer_bee', 'name': 'Killer Bee', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'gai', 'name': 'Maito Gai', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'minato', 'name': 'Minato Namikaze', 'stars': 5.0, 'price_yt': 2000, 'purchasable': True},
 {'id': 'naruto', 'name': 'Naruto Uzumaki', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'sasuke', 'name': 'Sasuke Uchiha', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'a_raikage', 'name': 'A — 4e Raikage', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'danzo', 'name': 'Danzo Shimura', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'gaara', 'name': 'Gaara', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'sarutobi', 'name': 'Hiruzen Sarutobi', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'kakashi', 'name': 'Kakashi Hatake', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'kisame', 'name': 'Kisame Hoshigaki', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'orochimaru', 'name': 'Orochimaru', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'tsunade', 'name': 'Tsunade', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'deidara', 'name': 'Deidara', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'hidan', 'name': 'Hidan', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'kabuto', 'name': 'Kabuto Yakushi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'neji', 'name': 'Neji Hyuga', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'rock_lee', 'name': 'Rock Lee', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'sakura', 'name': 'Sakura Haruno', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'sasori', 'name': 'Sasori', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'shikamaru', 'name': 'Shikamaru Nara', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'temari', 'name': 'Temari', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'choji', 'name': 'Choji Akimichi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'hinata', 'name': 'Hinata Hyuga', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'kankuro', 'name': 'Kankuro', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'kiba', 'name': 'Kiba Inuzuka', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'shino', 'name': 'Shino Aburame', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'suigetsu', 'name': 'Suigetsu Hozuki', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'tenten', 'name': 'Tenten', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'karin', 'name': 'Karin', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'onoki', 'name': 'Ônoki', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'gengetsu', 'name': 'Gengetsu Hozuki', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'tobirama', 'name': 'Tobirama Senju', 'stars': 5.0, 'price_yt': 2000, 'purchasable': True},
 {'id': 'zabuza', 'name': 'Zabuza Momochi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'shisui', 'name': 'Shisui Uchiwa', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'konohamaru', 'name': 'Konohamaru Sarutobi', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'haku', 'name': 'Haku', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'a3_raikage', 'name': 'A — 3e Raikage', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'chiyo', 'name': 'Chiyo', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'hanzo', 'name': 'Hanzo', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'kakuzu', 'name': 'Kakuzu', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'mei', 'name': 'Mei Terumi', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'ino', 'name': 'Ino Yamanaka', 'stars': 3.0, 'price_yt': 0, 'purchasable': False},
 {'id': 'kurenai', 'name': 'Kurenai Yûhi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'sai', 'name': 'Sai', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'ao', 'name': 'Ao', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'torune', 'name': 'Torune Aburame', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'mifune', 'name': 'Mifune', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'asuma', 'name': 'Asuma Sarutobi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'kushina', 'name': 'Kushina Uzumaki', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'rin', 'name': 'Rin Nohara', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'shizune', 'name': 'Shizune', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'kimimaro', 'name': 'Kimimaro', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'chojuro', 'name': 'Chôjûrô', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'konan', 'name': 'Konan', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'jugo', 'name': 'Jûgo', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'kurotsuchi', 'name': 'Kurotsuchi', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'mu', 'name': 'Mû', 'stars': 4.5, 'price_yt': 1500, 'purchasable': True},
 {'id': 'omoi', 'name': 'Omoi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'karui', 'name': 'Karui', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'anko', 'name': 'Anko Mitarashi', 'stars': 3.5, 'price_yt': 500, 'purchasable': True},
 {'id': 'yamato', 'name': 'Yamato', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'zetsu', 'name': 'Zetsu', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True},
 {'id': 'tobi', 'name': 'Tobi', 'stars': 4.0, 'price_yt': 1000, 'purchasable': True}]
CATALOG_BY_ID = {row["id"]: row for row in ECONOMY_CATALOG}
BASE_CARD_IDS = sorted([row["id"] for row in ECONOMY_CATALOG if float(row["stars"]) <= 3.0])


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


class DBConnection:
    """Petit adaptateur SQLite/PostgreSQL pour garder le reste du serveur simple."""
    def __init__(self, raw, backend: str):
        self.raw = raw
        self.backend = backend
        self.closed = False

    def execute(self, sql: str, params=()):
        if self.closed:
            raise RuntimeError("Connexion base de données fermée.")
        statement = str(sql)
        if self.backend == "postgres":
            if statement.strip().upper() == "BEGIN IMMEDIATE":
                statement = "BEGIN"
            statement = statement.replace("?", "%s")
        return self.raw.execute(statement, params)

    def executescript(self, script: str):
        # Les DDL du serveur ne contiennent pas de ';' dans des chaînes SQL.
        for statement in str(script).split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        if not self.closed:
            self.raw.close()
            self.closed = True


def _open_raw_db():
    if DB_BACKEND == "postgres":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:
            raise RuntimeError("psycopg est requis pour DATABASE_URL PostgreSQL.") from exc
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _ensure_schema(conn: DBConnection):
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(
          account_id TEXT PRIMARY KEY,
          google_sub TEXT UNIQUE NOT NULL,
          email TEXT,
          display_name TEXT,
          picture TEXT,
          pseudo TEXT UNIQUE,
          pseudo_norm TEXT UNIQUE,
          elo INTEGER NOT NULL DEFAULT 100,
          yt_balance INTEGER NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS owned_cards(
          account_id TEXT NOT NULL,
          card_id TEXT NOT NULL,
          purchased_at INTEGER NOT NULL,
          price_yt INTEGER NOT NULL,
          PRIMARY KEY(account_id, card_id)
        );
        CREATE TABLE IF NOT EXISTS solo_permits(
          permit TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          settled_at INTEGER,
          victory INTEGER,
          reward INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS economy_ledger(
          entry_id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          amount INTEGER NOT NULL,
          created_at INTEGER NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS card_instances(
          instance_id TEXT PRIMARY KEY,
          owner_id TEXT NOT NULL,
          card_id TEXT NOT NULL,
          original_potential INTEGER NOT NULL,
          current_potential INTEGER NOT NULL,
          overjet_hp INTEGER NOT NULL DEFAULT 0,
          overjet_tai INTEGER NOT NULL DEFAULT 0,
          overjet_nin INTEGER NOT NULL DEFAULT 0,
          overjet_gen INTEGER NOT NULL DEFAULT 0,
          acquisition_type TEXT NOT NULL DEFAULT 'SHOP',
          acquired_at INTEGER NOT NULL,
          price_paid INTEGER NOT NULL DEFAULT 0,
          listed INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_listings(
          listing_id TEXT PRIMARY KEY,
          instance_id TEXT NOT NULL UNIQUE,
          seller_id TEXT NOT NULL,
          card_id TEXT NOT NULL,
          price_yt INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          buyer_id TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          completed_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_owned_cards_account ON owned_cards(account_id);
        CREATE INDEX IF NOT EXISTS idx_solo_permits_account ON solo_permits(account_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_account ON economy_ledger(account_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_instances_owner ON card_instances(owner_id, card_id);
        CREATE INDEX IF NOT EXISTS idx_market_active ON market_listings(status, price_yt, created_at);
        CREATE INDEX IF NOT EXISTS idx_market_seller ON market_listings(seller_id, status, created_at);
        """)
        if conn.backend == "postgres":
            conn.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS elo INTEGER NOT NULL DEFAULT 100")
            conn.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS yt_balance INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS legacy_account_id TEXT")
        else:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
            if "elo" not in cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN elo INTEGER NOT NULL DEFAULT 100")
            if "yt_balance" not in cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN yt_balance INTEGER NOT NULL DEFAULT 0")
            if "legacy_account_id" not in cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN legacy_account_id TEXT")
        # Migration sans perte des anciennes possessions modèle -> exemplaires.
        for legacy in conn.execute("SELECT account_id,card_id,purchased_at,price_yt FROM owned_cards").fetchall():
            raw = f"{legacy['account_id']}|{legacy['card_id']}".encode("utf-8")
            iid = "LEGACY-" + hashlib.sha256(raw).hexdigest()[:32]
            potential = 90 + (int(hashlib.sha256(raw + b'|potential').hexdigest()[:8], 16) % 11)
            conn.execute("""INSERT INTO card_instances(instance_id,owner_id,card_id,original_potential,current_potential,
                         acquisition_type,acquired_at,price_paid,listed,updated_at)
                         SELECT ?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM card_instances WHERE instance_id=?)""",
                         (iid, legacy["account_id"], legacy["card_id"], potential, potential, "LEGACY_MIGRATION",
                          int(legacy["purchased_at"]), int(legacy["price_yt"]), 0, now(), iid))
        conn.commit()
        _SCHEMA_READY = True


def db():
    conn = DBConnection(_open_raw_db(), DB_BACKEND)
    _ensure_schema(conn)
    return conn


def _adopt_account_id(conn: DBConnection, current_id: str, desired_id: str):
    """Déplace l'identité et toutes ses données vers l'ancien account_id local."""
    if not desired_id or desired_id == current_id:
        if desired_id:
            conn.execute("UPDATE accounts SET legacy_account_id=? WHERE account_id=?", (desired_id, current_id))
        return current_id
    conn.execute("UPDATE accounts SET account_id=?,legacy_account_id=? WHERE account_id=?", (desired_id, desired_id, current_id))
    conn.execute("UPDATE sessions SET account_id=? WHERE account_id=?", (desired_id, current_id))
    conn.execute("UPDATE devices SET account_id=? WHERE account_id=?", (desired_id, current_id))
    conn.execute("UPDATE owned_cards SET account_id=? WHERE account_id=?", (desired_id, current_id))
    conn.execute("UPDATE card_instances SET owner_id=? WHERE owner_id=?", (desired_id, current_id))
    conn.execute("UPDATE market_listings SET seller_id=? WHERE seller_id=?", (desired_id, current_id))
    conn.execute("UPDATE market_listings SET buyer_id=? WHERE buyer_id=?", (desired_id, current_id))
    conn.execute("UPDATE solo_permits SET account_id=? WHERE account_id=?", (desired_id, current_id))
    conn.execute("UPDATE economy_ledger SET account_id=? WHERE account_id=?", (desired_id, current_id))
    return desired_id


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
        "yt_balance": int(row["yt_balance"] if "yt_balance" in row.keys() else 0),
    }



def _weekly_window(ts: int | None = None):
    dt = datetime.fromtimestamp(ts or now(), tz=timezone.utc)
    start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    iso = start.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    return week_key, int(start.timestamp()), int(end.timestamp())


def _weekly_seed(week_key: str, rarity_key: str) -> str:
    return hashlib.sha256(f"YUGITO|WEEKLY|{week_key}|{rarity_key}|V1".encode("utf-8")).hexdigest()


def _weekly_rank(seed: str, card_id: str) -> str:
    return hashlib.sha256(f"{seed}|{card_id}|YUGITO-WEEKLY-V1".encode("utf-8")).hexdigest()


def weekly_rotation_payload(ts: int | None = None):
    week_key, starts_at, ends_at = _weekly_window(ts)
    seeds = {rarity: _weekly_seed(week_key, rarity) for rarity in WEEKLY_COUNTS}
    card_ids = []
    for rarity_key, count in WEEKLY_COUNTS.items():
        rarity = float(rarity_key)
        pool = [row["id"] for row in ECONOMY_CATALOG if float(row["stars"]) == rarity]
        pool.sort(key=lambda cid, seed=seeds[rarity_key]: _weekly_rank(seed, cid))
        card_ids.extend(pool[:count])
    return {
        "ok": True,
        "version": 1,
        "week_key": week_key,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "next_rotation_unix": ends_at,
        "counts": dict(WEEKLY_COUNTS),
        "seeds": seeds,
        "card_ids": card_ids,
    }


def economy_state(conn, account_id: str, include_catalog: bool = True):
    acc = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    if not acc:
        return None
    instance_rows = conn.execute("SELECT * FROM card_instances WHERE owner_id=? ORDER BY acquired_at,instance_id", (account_id,)).fetchall()
    instances = [dict(r) for r in instance_rows]
    owned = []
    playable_owned = []
    for r in instances:
        if r["card_id"] not in owned:
            owned.append(r["card_id"])
        if not int(r.get("listed", 0)) and r["card_id"] not in playable_owned:
            playable_owned.append(r["card_id"])
    rotation = weekly_rotation_payload()
    free_ids = list(rotation["card_ids"])
    available = []
    for cid in BASE_CARD_IDS + playable_owned + free_ids:
        if cid not in available:
            available.append(cid)
    data = {
        "ok": True,
        "economy_available": True,
        "yt_balance": int(acc["yt_balance"]),
        "base_card_ids": list(BASE_CARD_IDS),
        "owned_card_ids": owned,
        "playable_owned_card_ids": playable_owned,
        "card_instances": instances,
        "free_card_ids": free_ids,
        "available_card_ids": available,
        "rotation": {
            "week_key": rotation["week_key"],
            "starts_at": rotation["starts_at"],
            "ends_at": rotation["ends_at"],
            "card_ids": free_ids,
        },
    }
    if include_catalog:
        data["catalog"] = ECONOMY_CATALOG
    return data


def _new_instance(conn, owner_id: str, card_id: str, price_paid: int, acquisition: str = "SHOP"):
    potential = secrets.randbelow(11) + 90
    iid = "CARD-" + secrets.token_hex(16)
    t = now()
    conn.execute("""INSERT INTO card_instances(instance_id,owner_id,card_id,original_potential,current_potential,
                 acquisition_type,acquired_at,price_paid,listed,updated_at) VALUES(?,?,?,?,?,?,?,?,0,?)""",
                 (iid, owner_id, card_id, potential, potential, acquisition, t, price_paid, t))
    return conn.execute("SELECT * FROM card_instances WHERE instance_id=?", (iid,)).fetchone()


def _listing_public(row):
    bonus_total = (
        int(row["overjet_hp"]) + int(row["overjet_tai"])
        + int(row["overjet_nin"]) + int(row["overjet_gen"])
    )
    total_percent = int(row["current_potential"]) + bonus_total
    return {
        "listing_id": row["listing_id"], "instance_id": row["instance_id"], "card_id": row["card_id"],
        "price_yt": int(row["price_yt"]), "status": row["status"], "seller_id": row["seller_id"],
        "seller_pseudo": row["seller_pseudo"] or "Ninja", "buyer_id": row["buyer_id"],
        "created_at": int(row["created_at"]), "updated_at": int(row["updated_at"]),
        "completed_at": row["completed_at"], "original_potential": int(row["original_potential"]),
        "current_potential": int(row["current_potential"]), "overjet_hp": int(row["overjet_hp"]),
        "overjet_tai": int(row["overjet_tai"]), "overjet_nin": int(row["overjet_nin"]),
        "overjet_gen": int(row["overjet_gen"]), "bonus_total": bonus_total,
        "total_percent": total_percent,
        "is_full": int(row["current_potential"]) >= 100 and bonus_total >= 10,
    }


def market_listings(conn, where="l.status='active'", params=(), order="l.price_yt ASC, l.created_at DESC", limit=100):
    rows = conn.execute(f"""SELECT l.*,i.original_potential,i.current_potential,i.overjet_hp,i.overjet_tai,
        i.overjet_nin,i.overjet_gen,a.pseudo AS seller_pseudo FROM market_listings l
        JOIN card_instances i ON i.instance_id=l.instance_id JOIN accounts a ON a.account_id=l.seller_id
        WHERE {where} ORDER BY {order} LIMIT ?""", tuple(params) + (limit,)).fetchall()
    return [_listing_public(r) for r in rows]


def _clean_old_solo_permits(conn):
    cutoff = now() - 7 * 24 * 60 * 60
    conn.execute("DELETE FROM solo_permits WHERE expires_at<? AND (settled_at IS NULL OR settled_at<?)", (now(), cutoff))


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
                recovery = b.get("recovery") if isinstance(b.get("recovery"), dict) else None
                conn=db()
                clash=conn.execute("SELECT * FROM accounts WHERE pseudo_norm=? AND account_id<>?",(normalize_pseudo(pseudo),acc["account_id"])).fetchone()
                if clash:
                    conn.close(); self._json(409,{"ok":False,"error":"Ce pseudo est déjà lié à un autre compte Google."}); return

                # Le registre historique MQTT peut déjà réserver ce pseudo sans
                # qu'il existe dans la nouvelle base Google. Sur PC, si l'utilisateur
                # saisit exactement SON ancien pseudo, le launcher envoie sa preuve
                # locale et on adopte l'ancien account_id afin de conserver l'identité.
                migrated_account_id = None
                recovered_local_identity = False

                # Recovery Godot 2.0.x : l'ancien account_id local peut être repris
                # uniquement si l'e-mail du profil local correspond EXACTEMENT au
                # compte Google authentifié. L'e-mail venant de Google est la preuve.
                if recovery:
                    rp = str(recovery.get("pseudo") or "").strip()
                    raid = str(recovery.get("account_id") or "").strip()
                    remail = str(recovery.get("email") or "").strip().casefold()
                    google_email = str(acc.get("email") or "").strip().casefold()
                    valid_aid = 8 <= len(raid) <= 128 and all(ch.isalnum() or ch in "-_" for ch in raid)
                    if normalize_pseudo(rp) != normalize_pseudo(pseudo) or not valid_aid or not remail or remail != google_email:
                        conn.close(); self._json(403,{"ok":False,"error":"La récupération locale ne correspond pas au compte Google connecté."}); return
                    used = conn.execute("SELECT google_sub FROM accounts WHERE account_id=? AND account_id<>?", (raid, acc["account_id"])).fetchone()
                    if used:
                        conn.close(); self._json(409,{"ok":False,"error":"Cet ancien compte YUGITO existe déjà sur le serveur."}); return
                    old_id = acc["account_id"]
                    aid_recovered = _adopt_account_id(conn, old_id, raid)
                    if aid_recovered != old_id:
                        migrated_account_id = aid_recovered
                    recovered_local_identity = True

                # Ancien flux historique MQTT (preuve secrète) conservé.
                if legacy and not recovered_local_identity:
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
                        aid_legacy = _adopt_account_id(conn, old_id, laid)
                        if aid_legacy != old_id:
                            migrated_account_id = aid_legacy

                aid=migrated_account_id or acc["account_id"]
                conn.execute("UPDATE accounts SET pseudo=?,pseudo_norm=?,updated_at=? WHERE account_id=?",(pseudo,normalize_pseudo(pseudo),now(),aid))
                conn.commit(); row=conn.execute("SELECT * FROM accounts WHERE account_id=?",(aid,)).fetchone(); conn.close()
                self._json(200,{"ok":True,"account":account_public(row),"migrated_legacy":bool(migrated_account_id),"recovered_local_identity":recovered_local_identity}); return
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
            if self.path == "/api/account/sync-elo":
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                b = self._body()
                elo = max(0, min(5000, int(b.get("elo") or 100)))
                conn = db()
                conn.execute("UPDATE accounts SET elo=?,updated_at=? WHERE account_id=?", (elo, now(), acc["account_id"]))
                conn.commit()
                row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (acc["account_id"],)).fetchone()
                conn.close()
                self._json(200, {"ok": True, "account": account_public(row)}); return
            if self.path == "/api/economy/purchase":
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                b = self._body(); cid = str(b.get("card_id") or "").strip()
                card = CATALOG_BY_ID.get(cid)
                if not card:
                    self._json(404, {"ok": False, "error": "Carte inconnue."}); return
                if not card.get("purchasable"):
                    self._json(400, {"ok": False, "error": "Cette carte est déjà disponible de base."}); return
                price = int(card["price_yt"])
                conn = db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT yt_balance FROM accounts WHERE account_id=?", (acc["account_id"],)).fetchone()
                    balance = int(row["yt_balance"] if row else 0)
                    if balance < price:
                        conn.rollback(); conn.close()
                        self._json(409, {"ok": False, "error": "YT insuffisants.", "yt_balance": balance, "price_yt": price}); return
                    conn.execute("UPDATE accounts SET yt_balance=yt_balance-?,updated_at=? WHERE account_id=?", (price, now(), acc["account_id"]))
                    instance = _new_instance(conn, acc["account_id"], cid, price)
                    conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",
                                 (secrets.token_hex(16), acc["account_id"], "purchase", -price, now(), json.dumps({"card_id": cid}, separators=(",", ":"))))
                    conn.commit()
                    state = economy_state(conn, acc["account_id"], True)
                finally:
                    conn.close()
                self._json(200, {"ok": True, "purchase": {"card_id": cid, "price_yt": price}, "instance": dict(instance), "state": state}); return
            if self.path == "/api/market/list":
                acc = auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                b=self._body(); iid=str(b.get("instance_id") or "").strip()
                try: price=int(b.get("price_yt") or 0)
                except Exception: price=0
                if price < MARKET_MIN_PRICE or price > MARKET_MAX_PRICE:
                    self._json(400,{"ok":False,"error":f"Prix autorisé : {MARKET_MIN_PRICE} à {MARKET_MAX_PRICE} YT."}); return
                conn=db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    count=conn.execute("SELECT COUNT(*) AS n FROM market_listings WHERE seller_id=? AND status='active'",(acc["account_id"],)).fetchone()
                    if int(count["n"]) >= MARKET_MAX_ACTIVE_LISTINGS:
                        conn.rollback(); self._json(409,{"ok":False,"error":f"Maximum {MARKET_MAX_ACTIVE_LISTINGS} ventes actives."}); return
                    inst=conn.execute("SELECT * FROM card_instances WHERE instance_id=? AND owner_id=?",(iid,acc["account_id"])).fetchone()
                    if not inst or int(inst["listed"]):
                        conn.rollback(); self._json(409,{"ok":False,"error":"Exemplaire introuvable ou déjà en vente."}); return
                    lid="SALE-"+secrets.token_hex(16); t=now()
                    conn.execute("INSERT INTO market_listings(listing_id,instance_id,seller_id,card_id,price_yt,status,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,?)",(lid,iid,acc["account_id"],inst["card_id"],price,t,t))
                    conn.execute("UPDATE card_instances SET listed=1,updated_at=? WHERE instance_id=?",(t,iid)); conn.commit()
                    listing=market_listings(conn,"l.listing_id=?",(lid,))[0]
                finally: conn.close()
                self._json(200,{"ok":True,"listing":listing}); return
            if self.path in ("/api/market/cancel", "/api/market/update-price"):
                acc=auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                b=self._body(); lid=str(b.get("listing_id") or "").strip(); conn=db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row=conn.execute("SELECT * FROM market_listings WHERE listing_id=? AND seller_id=? AND status='active'",(lid,acc["account_id"])).fetchone()
                    if not row: conn.rollback(); self._json(404,{"ok":False,"error":"Vente active introuvable."}); return
                    t=now()
                    if self.path.endswith("cancel"):
                        conn.execute("UPDATE market_listings SET status='cancelled',updated_at=?,completed_at=? WHERE listing_id=?",(t,t,lid))
                        conn.execute("UPDATE card_instances SET listed=0,updated_at=? WHERE instance_id=?",(t,row["instance_id"])); conn.commit()
                        self._json(200,{"ok":True,"cancelled":True,"listing_id":lid}); return
                    try: price=int(b.get("price_yt") or 0)
                    except Exception: price=0
                    if price < MARKET_MIN_PRICE or price > MARKET_MAX_PRICE:
                        conn.rollback(); self._json(400,{"ok":False,"error":f"Prix autorisé : {MARKET_MIN_PRICE} à {MARKET_MAX_PRICE} YT."}); return
                    conn.execute("UPDATE market_listings SET price_yt=?,updated_at=? WHERE listing_id=?",(price,t,lid)); conn.commit()
                finally: conn.close()
                self._json(200,{"ok":True,"listing_id":lid,"price_yt":price}); return
            if self.path == "/api/market/buy":
                acc=auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                lid=str(self._body().get("listing_id") or "").strip(); conn=db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    if conn.backend == "postgres":
                        sale=conn.execute("SELECT * FROM market_listings WHERE listing_id=? FOR UPDATE",(lid,)).fetchone()
                    else: sale=conn.execute("SELECT * FROM market_listings WHERE listing_id=?",(lid,)).fetchone()
                    if not sale or sale["status"] != "active": conn.rollback(); self._json(409,{"ok":False,"error":"Cette vente n'est plus disponible."}); return
                    if sale["seller_id"] == acc["account_id"]: conn.rollback(); self._json(400,{"ok":False,"error":"Tu ne peux pas acheter ta propre vente."}); return
                    price=int(sale["price_yt"]); buyer=conn.execute("SELECT yt_balance FROM accounts WHERE account_id=?",(acc["account_id"],)).fetchone()
                    inst=conn.execute("SELECT * FROM card_instances WHERE instance_id=? AND owner_id=? AND listed=1",(sale["instance_id"],sale["seller_id"])).fetchone()
                    if not inst: conn.rollback(); self._json(409,{"ok":False,"error":"L'exemplaire n'est plus transférable."}); return
                    if not buyer or int(buyer["yt_balance"]) < price: conn.rollback(); self._json(409,{"ok":False,"error":"YT insuffisants.","price_yt":price}); return
                    t=now(); seller=sale["seller_id"]
                    conn.execute("UPDATE accounts SET yt_balance=yt_balance-?,updated_at=? WHERE account_id=?",(price,t,acc["account_id"]))
                    conn.execute("UPDATE accounts SET yt_balance=yt_balance+?,updated_at=? WHERE account_id=?",(price,t,seller))
                    conn.execute("UPDATE card_instances SET owner_id=?,listed=0,acquisition_type='MARKET',acquired_at=?,price_paid=?,updated_at=? WHERE instance_id=?",(acc["account_id"],t,price,t,sale["instance_id"]))
                    conn.execute("UPDATE market_listings SET status='sold',buyer_id=?,updated_at=?,completed_at=? WHERE listing_id=?",(acc["account_id"],t,t,lid))
                    meta=json.dumps({"listing_id":lid,"instance_id":sale["instance_id"],"card_id":sale["card_id"]},separators=(",",":"))
                    conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",(secrets.token_hex(16),acc["account_id"],"market_buy",-price,t,meta))
                    conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",(secrets.token_hex(16),seller,"market_sale",price,t,meta))
                    conn.commit(); state=economy_state(conn,acc["account_id"],False)
                finally: conn.close()
                self._json(200,{"ok":True,"listing_id":lid,"instance_id":sale["instance_id"],"price_yt":price,"state":state}); return
            if self.path in ("/api/economy/train", "/api/economy/perfect", "/api/economy/change-art"):
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                b = self._body(); iid = str(b.get("instance_id") or "").strip()
                if not iid:
                    self._json(400, {"ok": False, "error": "Exemplaire manquant."}); return
                conn = db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    inst = conn.execute("SELECT * FROM card_instances WHERE instance_id=? AND owner_id=?", (iid, acc["account_id"])).fetchone()
                    if not inst:
                        conn.rollback(); self._json(404, {"ok": False, "error": "Exemplaire introuvable."}); return
                    if int(inst["listed"]):
                        conn.rollback(); self._json(409, {"ok": False, "error": "Impossible : cet exemplaire est en vente."}); return
                    balance_row = conn.execute("SELECT yt_balance FROM accounts WHERE account_id=?", (acc["account_id"],)).fetchone()
                    balance = int(balance_row["yt_balance"] if balance_row else 0)
                    t = now()

                    if self.path == "/api/economy/train":
                        cost = 100
                        old_p = int(inst["current_potential"])
                        if old_p >= 100:
                            conn.rollback(); self._json(409, {"ok": False, "error": "Potentiel déjà à 100 %."}); return
                        if balance < cost:
                            conn.rollback(); self._json(409, {"ok": False, "error": "YT insuffisants.", "yt_balance": balance}); return
                        success = secrets.randbelow(100) < 62
                        new_p = old_p + 1 if success else old_p
                        conn.execute("UPDATE accounts SET yt_balance=yt_balance-?,updated_at=? WHERE account_id=?", (cost, t, acc["account_id"]))
                        if success:
                            conn.execute("UPDATE card_instances SET current_potential=?,updated_at=? WHERE instance_id=?", (new_p, t, iid))
                        meta = {"instance_id": iid, "card_id": inst["card_id"], "old_potential": old_p, "new_potential": new_p, "outcome": "SUCCESS" if success else "FAIL"}
                        conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",
                                     (secrets.token_hex(16), acc["account_id"], "training", -cost, t, json.dumps(meta, separators=(",", ":"))))
                        conn.commit()
                        state = economy_state(conn, acc["account_id"], True)
                        self._json(200, {"ok": True, "kind": "training", "instance_id": iid, "cost_yt": cost,
                                         "outcome": meta["outcome"], "success": success, "old_potential": old_p, "new_potential": new_p, "state": state}); return

                    stat_map = {"hp": "overjet_hp", "tai": "overjet_tai", "nin": "overjet_nin", "gen": "overjet_gen"}
                    bonuses = {k: int(inst[v]) for k, v in stat_map.items()}
                    total_bonus = sum(bonuses.values())
                    if int(inst["current_potential"]) < 100:
                        conn.rollback(); self._json(409, {"ok": False, "error": "Potentiel 100 % requis."}); return

                    if self.path == "/api/economy/perfect":
                        cost = 200
                        stat = str(b.get("stat") or "").strip().lower()
                        if stat not in stat_map:
                            conn.rollback(); self._json(400, {"ok": False, "error": "Statistique invalide."}); return
                        if total_bonus >= 10:
                            conn.rollback(); self._json(409, {"ok": False, "error": "Perfection maximale : +10 % total. Utilise le Changement d'art."}); return
                        if balance < cost:
                            conn.rollback(); self._json(409, {"ok": False, "error": "YT insuffisants.", "yt_balance": balance}); return
                        old_bonus = bonuses[stat]
                        roll = secrets.randbelow(100)
                        if old_bonus <= 0:
                            outcome = "SUCCESS" if roll < 62 else "FAIL"
                        else:
                            outcome = "CRITICAL_FAIL" if roll < 28 else ("FAIL" if roll < 56 else "SUCCESS")
                        new_bonus = old_bonus
                        if outcome == "SUCCESS":
                            new_bonus = old_bonus + 1
                        elif outcome == "CRITICAL_FAIL":
                            new_bonus = max(0, old_bonus - 1)
                        conn.execute("UPDATE accounts SET yt_balance=yt_balance-?,updated_at=? WHERE account_id=?", (cost, t, acc["account_id"]))
                        conn.execute(f"UPDATE card_instances SET {stat_map[stat]}=?,updated_at=? WHERE instance_id=?", (new_bonus, t, iid))
                        meta = {"instance_id": iid, "card_id": inst["card_id"], "stat": stat, "old_bonus": old_bonus, "new_bonus": new_bonus, "outcome": outcome}
                        conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",
                                     (secrets.token_hex(16), acc["account_id"], "perfection", -cost, t, json.dumps(meta, separators=(",", ":"))))
                        conn.commit()
                        state = economy_state(conn, acc["account_id"], True)
                        self._json(200, {"ok": True, "kind": "perfection", "instance_id": iid, "cost_yt": cost, "stat": stat,
                                         "outcome": outcome, "old_bonus": old_bonus, "new_bonus": new_bonus, "state": state}); return

                    # Changement d'art : disponible à +10 total. Le hasard et le débit restent serveur.
                    cost = 200
                    source = str(b.get("source_stat") or "").strip().lower()
                    target = str(b.get("target_stat") or "").strip().lower()
                    if source not in stat_map or target not in stat_map or source == target:
                        conn.rollback(); self._json(400, {"ok": False, "error": "Statistiques source/cible invalides."}); return
                    if total_bonus < 10:
                        conn.rollback(); self._json(409, {"ok": False, "error": "Changement d'art disponible à +10 % total."}); return
                    if bonuses[source] <= 0:
                        conn.rollback(); self._json(409, {"ok": False, "error": "La statistique source n'a aucun point à transférer."}); return
                    if balance < cost:
                        conn.rollback(); self._json(409, {"ok": False, "error": "YT insuffisants.", "yt_balance": balance}); return
                    source_old = bonuses[source]; target_old = bonuses[target]
                    roll = secrets.randbelow(100)
                    outcome = "CRITICAL_FAIL" if roll < 28 else ("FAIL" if roll < 56 else "SUCCESS")
                    source_new = source_old; target_new = target_old
                    if outcome == "SUCCESS":
                        source_new -= 1; target_new += 1
                    elif outcome == "CRITICAL_FAIL":
                        source_new -= 1
                    conn.execute("UPDATE accounts SET yt_balance=yt_balance-?,updated_at=? WHERE account_id=?", (cost, t, acc["account_id"]))
                    conn.execute(f"UPDATE card_instances SET {stat_map[source]}=?,{stat_map[target]}=?,updated_at=? WHERE instance_id=?",
                                 (source_new, target_new, t, iid))
                    meta = {"instance_id": iid, "card_id": inst["card_id"], "source_stat": source, "target_stat": target,
                            "source_old": source_old, "source_new": source_new, "target_old": target_old, "target_new": target_new, "outcome": outcome}
                    conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",
                                 (secrets.token_hex(16), acc["account_id"], "change_art", -cost, t, json.dumps(meta, separators=(",", ":"))))
                    conn.commit()
                    state = economy_state(conn, acc["account_id"], True)
                    self._json(200, {"ok": True, "kind": "change_art", "instance_id": iid, "cost_yt": cost, "outcome": outcome,
                                     "source_stat": source, "target_stat": target, "source_old": source_old, "source_new": source_new,
                                     "target_old": target_old, "target_new": target_new, "state": state}); return
                except Exception:
                    try: conn.rollback()
                    except Exception: pass
                    raise
                finally:
                    conn.close()
            if self.path == "/api/economy/solo/start":
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                permit = secrets.token_urlsafe(32); t = now()
                conn = db(); _clean_old_solo_permits(conn)
                conn.execute("INSERT INTO solo_permits(permit,account_id,created_at,expires_at) VALUES(?,?,?,?)",
                             (permit, acc["account_id"], t, t + SOLO_PERMIT_TTL))
                conn.commit(); conn.close()
                self._json(200, {"ok": True, "permit": permit, "expires_at": t + SOLO_PERMIT_TTL}); return
            if self.path == "/api/economy/solo/settle":
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                b = self._body(); permit = str(b.get("permit") or "").strip(); victory = bool(b.get("victory", False))
                if not permit:
                    self._json(400, {"ok": False, "error": "Permis Solo manquant."}); return
                conn = db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    p = conn.execute("SELECT * FROM solo_permits WHERE permit=? AND account_id=?", (permit, acc["account_id"])).fetchone()
                    if not p:
                        conn.rollback(); conn.close(); self._json(404, {"ok": False, "error": "Permis Solo invalide."}); return
                    if p["settled_at"] is not None:
                        reward = int(p["reward"] or 0)
                        conn.rollback()
                        state = economy_state(conn, acc["account_id"], True)
                        self._json(200, {"ok": True, "duplicate": True, "reward": reward, "state": state}); return
                    if int(p["expires_at"]) <= now():
                        conn.rollback(); conn.close(); self._json(410, {"ok": False, "error": "Permis Solo expiré."}); return
                    reward = SOLO_WIN_YT if victory else SOLO_LOSS_YT
                    t = now()
                    conn.execute("UPDATE solo_permits SET settled_at=?,victory=?,reward=? WHERE permit=?", (t, 1 if victory else 0, reward, permit))
                    if reward:
                        conn.execute("UPDATE accounts SET yt_balance=yt_balance+?,updated_at=? WHERE account_id=?", (reward, t, acc["account_id"]))
                        conn.execute("INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",
                                     (secrets.token_hex(16), acc["account_id"], "solo_win", reward, t, json.dumps({"permit": permit}, separators=(",", ":"))))
                    conn.commit()
                    state = economy_state(conn, acc["account_id"], True)
                finally:
                    conn.close()
                self._json(200, {"ok": True, "duplicate": False, "victory": victory, "reward": reward, "message": f"+{reward} YT" if reward else "0 YT", "state": state}); return
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
            if u.path == "/health":
                try:
                    hc = db(); hc.execute("SELECT 1").fetchone(); hc.close()
                    self._json(200,{"ok":True,"service":"YUGITO Auth","version":"persistent-2.2.0-progression-market","economy":True,"market":True,"database_backend":DB_BACKEND,"persistent":bool(DATABASE_URL),"time":now()})
                except Exception as exc:
                    self._json(503,{"ok":False,"service":"YUGITO Auth","version":"persistent-2.2.0-progression-market","economy":True,"market":True,"database_backend":DB_BACKEND,"persistent":bool(DATABASE_URL),"database_error":str(exc),"time":now()})
                return
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
            if u.path == "/api/collection/weekly":
                self._json(200, weekly_rotation_payload()); return
            if u.path == "/api/economy/state":
                acc = auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                conn = db(); state = economy_state(conn, acc["account_id"], True); conn.close()
                if not state:
                    self._json(404, {"ok": False, "error": "Compte introuvable."}); return
                self._json(200, state); return
            if u.path in ("/api/market/listings", "/api/market/mine", "/api/market/history"):
                acc=auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                conn=db()
                try:
                    limit=max(1,min(100,int((q.get("limit") or ["60"])[0])))
                    if u.path == "/api/market/mine":
                        listings=market_listings(conn,"l.seller_id=? AND l.status='active'",(acc["account_id"],),"l.created_at DESC",limit)
                    elif u.path == "/api/market/history":
                        listings=market_listings(conn,"(l.seller_id=? OR l.buyer_id=?) AND l.status<>'active'",(acc["account_id"],acc["account_id"]),"l.updated_at DESC",limit)
                    else:
                        clauses=["l.status='active'"]; params=[]
                        cid=str((q.get("card_id") or [""])[0]).strip()
                        if cid: clauses.append("l.card_id=?"); params.append(cid)
                        search=str((q.get("search") or [""])[0]).strip().casefold()
                        if search:
                            matching_ids = [
                                card["id"] for card in ECONOMY_CATALOG
                                if search in str(card["id"]).casefold() or search in str(card["name"]).casefold()
                            ]
                            if matching_ids:
                                clauses.append("l.card_id IN (" + ",".join("?" for _ in matching_ids) + ")")
                                params.extend(matching_ids)
                            else:
                                clauses.append("1=0")
                        minp=max(0,int((q.get("min_price") or ["0"])[0])); maxp=max(0,int((q.get("max_price") or ["0"])[0])); minpot=max(0,min(100,int((q.get("min_potential") or ["0"])[0])))
                        minpercent_raw=int((q.get("min_percent") or ["0"])[0])
                        minpercent=0 if minpercent_raw <= 0 else max(90,min(110,minpercent_raw))
                        full_only=str((q.get("full_only") or ["0"])[0]).strip().casefold() in ("1","true","yes","oui")
                        total_percent_sql="(i.current_potential+i.overjet_hp+i.overjet_tai+i.overjet_nin+i.overjet_gen)"
                        if minp: clauses.append("l.price_yt>=?"); params.append(minp)
                        if maxp: clauses.append("l.price_yt<=?"); params.append(maxp)
                        if minpot: clauses.append("i.current_potential>=?"); params.append(minpot)
                        if minpercent: clauses.append(total_percent_sql+">=?"); params.append(minpercent)
                        if full_only:
                            clauses.append("i.current_potential>=100")
                            clauses.append("(i.overjet_hp+i.overjet_tai+i.overjet_nin+i.overjet_gen)>=10")
                        sort=str((q.get("sort") or ["price_asc"])[0]); orders={"price_asc":"l.price_yt ASC,l.created_at DESC","price_desc":"l.price_yt DESC,l.created_at DESC","newest":"l.created_at DESC","potential":total_percent_sql+" DESC,l.price_yt ASC"}
                        listings=market_listings(conn," AND ".join(clauses),tuple(params),orders.get(sort,orders["price_asc"]),limit)
                finally: conn.close()
                self._json(200,{"ok":True,"listings":listings,"count":len(listings),"max_active_per_seller":MARKET_MAX_ACTIVE_LISTINGS,"min_price":MARKET_MIN_PRICE,"max_price":MARKET_MAX_PRICE}); return
            if u.path == "/api/account/me":
                acc=auth_account(self.headers)
                if not acc: self._json(401,{"ok":False,"error":"Session invalide."}); return
                self._json(200,{"ok":True,"account":account_public(acc)}); return
            self._json(404,{"ok":False,"error":"Not found"})
        except Exception as e:
            self._html(500,"<h1>Erreur YUGITO</h1><p>"+str(e).replace("<","&lt;")+"</p>")


def main():
    missing = []
    if not PUBLIC_BASE:
        missing.append("YUGITO_PUBLIC_BASE_URL (ou RENDER_EXTERNAL_URL)")
    if not GOOGLE_CLIENT_ID:
        missing.append("GOOGLE_CLIENT_ID")
    if not GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_CLIENT_SECRET")
    # Sécurité anti-perte : sur Render, on refuse désormais de démarrer sur SQLite.
    # Ainsi un déploiement mal configuré ne peut plus créer silencieusement une base éphémère.
    if IS_RENDER and not DATABASE_URL:
        missing.append("DATABASE_URL (PostgreSQL persistant)")
    if missing:
        print("ERREUR: variables d'environnement manquantes : " + ", ".join(missing))
        raise SystemExit(2)

    db().close()
    print(f"YUGITO Auth 2.2.0 PROGRESSION+MARKET sur 0.0.0.0:{PORT} -> {PUBLIC_BASE} | DB={DB_BACKEND} | persistent={bool(DATABASE_URL)}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
