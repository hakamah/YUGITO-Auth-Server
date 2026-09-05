from __future__ import annotations

import json
import secrets
import sys
import urllib.parse

import yugito_auth_server_core as core
from yugito_auth_server_core import *

# Keep an immutable reference to the exact stable production handler and its
# original do_POST implementation.  WIN_CHAIN must never replace Google/Auth.
_stable_handler_class = core.Handler
_original_do_post = core.Handler.do_POST


def _probe_device_start(self):
    """Exact device/start response path validated by the working Google APK."""
    if self.path != "/api/device/start":
        return _original_do_post(self)

    conn = None
    try:
        print("[DEVICE_START_PROBE] ENTER", flush=True)
        body = self._body()
        code = secrets.token_urlsafe(32)
        conn = core.db()
        print("[DEVICE_START_PROBE] DB_OPEN", flush=True)
        conn.execute(
            "INSERT INTO devices(device_code,platform,created_at,expires_at) VALUES(?,?,?,?)",
            (code, str(body.get("platform") or "unknown")[:20], core.now(), core.now() + core.DEVICE_TTL),
        )
        conn.commit()
        print("[DEVICE_START_PROBE] COMMIT_OK", flush=True)

        payload = {
            "ok": True,
            "device_code": code,
            "verification_url": core.PUBLIC_BASE + "/login?device_code=" + urllib.parse.quote(code),
            "google_client_id": core.GOOGLE_CLIENT_ID,
            "native_google": True,
            "expires_in": core.DEVICE_TTL,
            "interval": 2,
        }
        raw = core.json_bytes(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        print("[DEVICE_START_PROBE] HEADERS_SENT len=" + str(len(raw)), flush=True)
        self.wfile.write(raw)
        self.wfile.flush()
        self.close_connection = True
        print("[DEVICE_START_PROBE] BODY_FLUSHED_CONNECTION_CLOSE", flush=True)
        return
    except Exception as exc:
        print("[DEVICE_START_PROBE] ERROR " + repr(exc), flush=True)
        try:
            self._json(500, {"ok": False, "error": str(exc)})
        except Exception as exc2:
            print("[DEVICE_START_PROBE] ERROR_RESPONSE " + repr(exc2), flush=True)
        return
    finally:
        if conn is not None:
            try:
                conn.close()
                print("[DEVICE_START_PROBE] DB_CLOSE_OK", flush=True)
            except Exception as exc:
                print("[DEVICE_START_PROBE] DB_CLOSE_ERROR " + repr(exc), flush=True)


# Patch only the stable base handler's device/start method.  This is the exact
# mechanism that is live today and made Credential Manager reliable.
_stable_handler_class.do_POST = _probe_device_start

# WIN_CHAIN_V1 was originally written against a server revision that exposed
# this cleanup helper.  The known-good stable Google core does not.  Provide a
# tiny compatibility implementation instead of modifying the stable core.
def _clean_old_solo_permits_compat(conn):
    conn.execute(
        "DELETE FROM solo_permits WHERE settled_at IS NULL AND expires_at<=?",
        (core.now(),),
    )


if not hasattr(core, "_clean_old_solo_permits"):
    core._clean_old_solo_permits = _clean_old_solo_permits_compat

# The historical WIN_CHAIN_V1 module imports `yugito_auth_server as base`.
# When this file is executed as a script its module name is __main__, so bind
# that import explicitly to the stable core instead of recursively importing
# this wrapper a second time.
sys.modules["yugito_auth_server"] = core
import yugito_win_chain_server as win_chain

# Current mobile 2.1.5 allows Solo decks independently of the account's
# permanent collection.  The historical WIN_CHAIN validator assumed every
# card used in battle had to exist in owned_cards, which rejects legitimate
# current APK decks (fresh accounts can have 0 permanent cards).  Keep the
# important server-side checks — 8 unique known cards, star cap and per-rarity
# limits — but do not require permanent ownership for reward calculation.
def _deck_context_mobile_compat(conn, account_id, raw_deck):
    if raw_deck is None:
        return 32.5, []
    if not isinstance(raw_deck, list):
        raise ValueError("Deck invalide.")
    ids = [str(x or "").strip() for x in raw_deck]
    if len(ids) != 8:
        raise ValueError("Le deck doit contenir exactement 8 cartes.")
    if any(not x for x in ids) or len(set(ids)) != 8:
        raise ValueError("Deck invalide ou carte en double.")

    cards = []
    for cid in ids:
        card = core.CATALOG_BY_ID.get(cid)
        if not card:
            raise ValueError("Carte inconnue dans le deck.")
        cards.append(card)

    total = sum(float(c["stars"]) for c in cards)
    if total > 32.5001:
        raise ValueError("Le deck dépasse 32,5★.")
    limits = {3.5: 4, 4.0: 3, 4.5: 2, 5.0: 1}
    for stars, limit in limits.items():
        if sum(1 for c in cards if abs(float(c["stars"]) - stars) < 0.01) > limit:
            raise ValueError("Le deck ne respecte pas les limites d'étoiles.")
    return round(total, 1), ids


win_chain._deck_context = _deck_context_mobile_compat

_WIN_CHAIN_ROUTES = {
    "/api/economy/solo/start",
    "/api/economy/solo/settle",
    "/api/profile/multiplayer/record",
}
_WIN_CHAIN_HEADER = "X-Yugito-Win-Chain"


class Handler(win_chain.Handler):
    """Compatibility gate: old APKs remain on the stable economy behaviour.

    WIN_CHAIN_V1 is entered only by a client that explicitly sends
    `X-Yugito-Win-Chain: 1`.  Google/Auth routes are never intercepted here.
    """

    def _json(self, code, payload):
        if int(code) >= 400 and self.path in _WIN_CHAIN_ROUTES:
            print("[WIN_CHAIN_ERROR] path=%s code=%s payload=%r" % (self.path, code, payload), flush=True)
        return super()._json(code, payload)

    def do_POST(self):
        if self.path in _WIN_CHAIN_ROUTES:
            enabled = str(self.headers.get(_WIN_CHAIN_HEADER, "")).strip() == "1"
            if not enabled:
                return _stable_handler_class.do_POST(self)
            print("[WIN_CHAIN_SAFE] " + self.path, flush=True)
        return super().do_POST()


core.Handler = Handler


def main():
    core.main()


if __name__ == "__main__":
    main()
