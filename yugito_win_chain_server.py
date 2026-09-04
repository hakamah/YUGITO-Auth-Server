from __future__ import annotations

import hashlib
import json
import secrets

import yugito_auth_server as base

# YUGITO GC • WIN_CHAIN_V1
# Rules:
# - Solo: 10 -> 20 -> ... -> 100 YT (10 wins)
# - Classic: 30 -> 60 -> 90 -> 120 -> 150 YT (5 wins)
# - Ranked: 30 -> 60 -> ... -> 210 YT (7 wins)
# - Winner deck bonus: 24.5★ or less = +20%, 28.5★ = +10%, 32.5★ = 0%
#   Linear slope: 2.5 percentage points per star, capped [0%, 20%].
# - Final YT is always integer, rounded to nearest; exact .5 rounds upward.
# - Any loss/abandon/disconnect resets that mode's streak.
# - A new app process sends a new chain_session_id; changing it resets the streak.
# - Existing anti-farm: first 3 games vs same opponent full, 4th 50%, 5th 25%, 6th+ 0%.
#   From the 4th repeated opponent game onward, a win does NOT advance the streak.

WIN_CAPS = {"solo": 10, "classic": 5, "ranked": 7}
WIN_UNIT_YT = {"solo": 10, "classic": 30, "ranked": 30}
MULTI_LOSS_YT = 10
MIN_CLEAN_SECONDS = 90.0
MIN_CLEAN_TURNS = 4
ANTI_FARM_WINDOW_SECONDS = 6 * 60 * 60


def _ensure_win_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS win_streaks(
          account_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          chain_session_id TEXT NOT NULL DEFAULT '',
          streak INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY(account_id, mode)
        );
        CREATE TABLE IF NOT EXISTS solo_reward_context(
          permit TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          chain_session_id TEXT NOT NULL DEFAULT '',
          deck_stars REAL NOT NULL DEFAULT 32.5,
          deck_json TEXT NOT NULL DEFAULT '[]',
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS multiplayer_reward_events(
          event_id TEXT NOT NULL,
          account_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          victory INTEGER NOT NULL,
          reward INTEGER NOT NULL DEFAULT 0,
          player_stars REAL NOT NULL DEFAULT 32.5,
          opponent_key TEXT NOT NULL DEFAULT '',
          anti_farm_quarters INTEGER NOT NULL DEFAULT 4,
          streak_after INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL,
          PRIMARY KEY(event_id, account_id)
        );
        CREATE TABLE IF NOT EXISTS multiplayer_opponent_events(
          id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          opponent_key TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_win_streak_account ON win_streaks(account_id, mode);
        CREATE INDEX IF NOT EXISTS idx_multi_reward_account ON multiplayer_reward_events(account_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_multi_opp_account ON multiplayer_opponent_events(account_id, mode, opponent_key, created_at);
        """
    )


def _safe_session_id(value) -> str:
    s = str(value or "").strip()
    if not s:
        return "legacy"
    return s[:96]


def _get_streak(conn, account_id: str, mode: str, chain_session_id: str) -> int:
    sid = _safe_session_id(chain_session_id)
    row = conn.execute(
        "SELECT chain_session_id,streak FROM win_streaks WHERE account_id=? AND mode=?",
        (account_id, mode),
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO win_streaks(account_id,mode,chain_session_id,streak,updated_at) VALUES(?,?,?,?,?)",
            (account_id, mode, sid, 0, base.now()),
        )
        return 0
    old_sid = str(row["chain_session_id"] or "")
    streak = int(row["streak"] or 0)
    if sid != old_sid:
        conn.execute(
            "UPDATE win_streaks SET chain_session_id=?,streak=0,updated_at=? WHERE account_id=? AND mode=?",
            (sid, base.now(), account_id, mode),
        )
        return 0
    return max(0, streak)


def _set_streak(conn, account_id: str, mode: str, chain_session_id: str, streak: int) -> int:
    sid = _safe_session_id(chain_session_id)
    value = max(0, int(streak))
    conn.execute(
        """INSERT INTO win_streaks(account_id,mode,chain_session_id,streak,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(account_id,mode) DO UPDATE SET
             chain_session_id=excluded.chain_session_id,
             streak=excluded.streak,
             updated_at=excluded.updated_at""",
        (account_id, mode, sid, value, base.now()),
    )
    return value


def _stars_x2(stars: float) -> int:
    # YUGITO decks are built from 0.5-star increments; avoid floating rounding ambiguity.
    return int(float(stars) * 2.0 + 0.5)


def _deck_bonus_bp(stars: float) -> int:
    # 32.5★ = 65 half-stars. Every 0.5★ below gives +1.25% = 125 basis points.
    # 24.5★ = 49 half-stars => 16 * 125 = 2000 bp = +20%.
    return max(0, min(2000, (65 - _stars_x2(stars)) * 125))


def _round_half_up_ratio(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("Dénominateur de récompense invalide.")
    if numerator <= 0:
        return 0
    return (int(numerator) + int(denominator) // 2) // int(denominator)


def _base_win_reward(mode: str, streak: int) -> int:
    cap = WIN_CAPS[mode]
    unit = WIN_UNIT_YT[mode]
    return unit * min(max(1, int(streak)), cap)


def _winner_reward(mode: str, streak: int, deck_stars: float, anti_farm_quarters: int = 4) -> tuple[int, int, int]:
    base_amount = _base_win_reward(mode, streak)
    bonus_bp = _deck_bonus_bp(deck_stars)
    quarters = max(0, min(4, int(anti_farm_quarters)))
    # One single final rounding: chain base -> star bonus -> anti-farm.
    # denominator = 10,000 basis points * 4 anti-farm quarters.
    reward = _round_half_up_ratio(base_amount * (10000 + bonus_bp) * quarters, 40000)
    return reward, base_amount, bonus_bp


def _loss_reward(anti_farm_quarters: int) -> int:
    quarters = max(0, min(4, int(anti_farm_quarters)))
    return _round_half_up_ratio(MULTI_LOSS_YT * quarters, 4)


def _deck_context(conn, account_id: str, raw_deck) -> tuple[float, list[str]]:
    # Backward-compatible clients without a deck receive no star bonus.
    if raw_deck is None:
        return 32.5, []
    if not isinstance(raw_deck, list):
        raise ValueError("Deck invalide.")
    ids = [str(x or "").strip() for x in raw_deck]
    if len(ids) != 8:
        raise ValueError("Le deck doit contenir exactement 8 cartes.")
    if any(not x for x in ids) or len(set(ids)) != 8:
        raise ValueError("Deck invalide ou carte en double.")

    state = base.economy_state(conn, account_id, False)
    if not state:
        raise ValueError("Compte YUGITO introuvable.")
    available = set(str(x) for x in state.get("available_card_ids", []))
    cards = []
    for cid in ids:
        card = base.CATALOG_BY_ID.get(cid)
        if not card:
            raise ValueError("Carte inconnue dans le deck.")
        if cid not in available:
            raise ValueError("Une carte du deck n'est pas disponible sur ce compte.")
        cards.append(card)

    total = sum(float(c["stars"]) for c in cards)
    if total > 32.5001:
        raise ValueError("Le deck dépasse 32,5★.")
    limits = {3.5: 4, 4.0: 3, 4.5: 2, 5.0: 1}
    for stars, limit in limits.items():
        if sum(1 for c in cards if abs(float(c["stars"]) - stars) < 0.01) > limit:
            raise ValueError("Le deck ne respecte pas les limites d'étoiles.")
    return round(total, 1), ids


def _anti_farm_quarters(conn, account_id: str, mode: str, opponent_key: str) -> tuple[int, int]:
    key = str(opponent_key or "").strip().casefold()[:96]
    if not key:
        return 4, 0
    cutoff = base.now() - ANTI_FARM_WINDOW_SECONDS
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM multiplayer_opponent_events
           WHERE account_id=? AND mode=? AND opponent_key=? AND created_at>=?""",
        (account_id, mode, key, cutoff),
    ).fetchone()
    count = int(row["n"] if row else 0)
    if count <= 2:
        return 4, count
    if count == 3:
        return 2, count
    if count == 4:
        return 1, count
    return 0, count


def _record_opponent(conn, account_id: str, mode: str, opponent_key: str) -> None:
    key = str(opponent_key or "").strip().casefold()[:96]
    if not key:
        return
    conn.execute(
        "INSERT INTO multiplayer_opponent_events(id,account_id,mode,opponent_key,created_at) VALUES(?,?,?,?,?)",
        (secrets.token_hex(16), account_id, mode, key, base.now()),
    )


def _credit_account(conn, account_id: str, amount: int, kind: str, metadata: dict) -> None:
    amount = int(amount)
    if amount <= 0:
        return
    t = base.now()
    conn.execute("UPDATE accounts SET yt_balance=yt_balance+?,updated_at=? WHERE account_id=?", (amount, t, account_id))
    conn.execute(
        "INSERT INTO economy_ledger(entry_id,account_id,kind,amount,created_at,metadata_json) VALUES(?,?,?,?,?,?)",
        (secrets.token_hex(16), account_id, kind, amount, t, json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))),
    )


def _reward_message(mode: str, victory: bool, reward: int, streak: int, stars: float, bonus_bp: int, quarters: int, advanced: bool = True) -> str:
    if not victory:
        return "+%d YT • chaîne %s brisée" % (reward, mode.upper()) if reward else "0 YT • chaîne %s brisée" % mode.upper()
    bonus_text = ("+%.2f%%" % (bonus_bp / 100.0)).rstrip("0").rstrip(".")
    parts = ["+%d YT" % reward, "chaîne x%d" % streak, "deck %.1f★" % stars, "bonus étoiles %s" % bonus_text]
    if quarters < 4:
        parts.append("anti-farm %d%%" % (quarters * 25))
        if not advanced:
            parts.append("chaîne non augmentée")
    return " • ".join(parts)


class Handler(base.Handler):
    def do_POST(self):
        try:
            if self.path == "/api/economy/solo/start":
                acc = base.auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                body = self._body()
                chain_session_id = _safe_session_id(body.get("chain_session_id"))
                conn = base.db()
                try:
                    _ensure_win_schema(conn)
                    base._clean_old_solo_permits(conn)
                    # Any previous unfinished Solo game breaks the Solo chain.
                    previous = conn.execute(
                        "SELECT permit FROM solo_permits WHERE account_id=? AND settled_at IS NULL LIMIT 1",
                        (acc["account_id"],),
                    ).fetchone()
                    if previous:
                        _set_streak(conn, acc["account_id"], "solo", chain_session_id, 0)
                    current_streak = _get_streak(conn, acc["account_id"], "solo", chain_session_id)
                    deck_stars, deck_ids = _deck_context(conn, acc["account_id"], body.get("deck") if "deck" in body else None)
                    permit = secrets.token_urlsafe(32)
                    t = base.now()
                    conn.execute(
                        "INSERT INTO solo_permits(permit,account_id,created_at,expires_at) VALUES(?,?,?,?)",
                        (permit, acc["account_id"], t, t + base.SOLO_PERMIT_TTL),
                    )
                    conn.execute(
                        "INSERT INTO solo_reward_context(permit,account_id,chain_session_id,deck_stars,deck_json,created_at) VALUES(?,?,?,?,?,?)",
                        (permit, acc["account_id"], chain_session_id, deck_stars, json.dumps(deck_ids, separators=(",", ":")), t),
                    )
                    next_reward, next_base, next_bonus = _winner_reward("solo", current_streak + 1, deck_stars, 4)
                    conn.commit()
                finally:
                    conn.close()
                self._json(200, {
                    "ok": True,
                    "permit": permit,
                    "expires_at": t + base.SOLO_PERMIT_TTL,
                    "deck_stars": deck_stars,
                    "star_bonus_bp": next_bonus,
                    "streak": current_streak,
                    "next_win_base_yt": next_base,
                    "next_win_reward_yt": next_reward,
                }); return

            if self.path == "/api/economy/solo/settle":
                acc = base.auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                body = self._body()
                permit = str(body.get("permit") or "").strip()
                victory = bool(body.get("victory", False))
                if not permit:
                    self._json(400, {"ok": False, "error": "Permis Solo manquant."}); return
                conn = base.db()
                try:
                    _ensure_win_schema(conn)
                    conn.execute("BEGIN IMMEDIATE")
                    p = conn.execute("SELECT * FROM solo_permits WHERE permit=? AND account_id=?", (permit, acc["account_id"])).fetchone()
                    if not p:
                        conn.rollback(); self._json(404, {"ok": False, "error": "Permis Solo invalide."}); return
                    ctx = conn.execute("SELECT * FROM solo_reward_context WHERE permit=?", (permit,)).fetchone()
                    session_id = _safe_session_id(ctx["chain_session_id"] if ctx else "legacy")
                    deck_stars = float(ctx["deck_stars"] if ctx else 32.5)
                    if p["settled_at"] is not None:
                        reward = int(p["reward"] or 0)
                        streak = _get_streak(conn, acc["account_id"], "solo", session_id)
                        conn.rollback()
                        state = base.economy_state(conn, acc["account_id"], True)
                        self._json(200, {"ok": True, "duplicate": True, "reward": reward, "streak": streak, "state": state}); return
                    if int(p["expires_at"]) <= base.now():
                        _set_streak(conn, acc["account_id"], "solo", session_id, 0)
                        conn.commit()
                        self._json(410, {"ok": False, "error": "Permis Solo expiré.", "streak": 0}); return

                    current = _get_streak(conn, acc["account_id"], "solo", session_id)
                    bonus_bp = 0
                    base_amount = 0
                    if victory:
                        streak = _set_streak(conn, acc["account_id"], "solo", session_id, current + 1)
                        reward, base_amount, bonus_bp = _winner_reward("solo", streak, deck_stars, 4)
                    else:
                        streak = _set_streak(conn, acc["account_id"], "solo", session_id, 0)
                        reward = 0

                    t = base.now()
                    conn.execute("UPDATE solo_permits SET settled_at=?,victory=?,reward=? WHERE permit=?", (t, 1 if victory else 0, reward, permit))
                    meta = {"permit": permit, "mode": "solo", "streak": streak, "deck_stars": deck_stars, "star_bonus_bp": bonus_bp, "win_chain_base": base_amount}
                    _credit_account(conn, acc["account_id"], reward, "solo_win" if victory else "solo_loss", meta)
                    conn.commit()
                    state = base.economy_state(conn, acc["account_id"], True)
                finally:
                    conn.close()
                message = _reward_message("solo", victory, reward, streak, deck_stars, bonus_bp, 4)
                self._json(200, {
                    "ok": True,
                    "duplicate": False,
                    "victory": victory,
                    "reward": reward,
                    "streak": streak,
                    "deck_stars": deck_stars,
                    "star_bonus_bp": bonus_bp,
                    "win_chain_base": base_amount,
                    "message": message,
                    "state": state,
                }); return

            if self.path == "/api/profile/multiplayer/record":
                acc = base.auth_account(self.headers)
                if not acc:
                    self._json(401, {"ok": False, "error": "Session invalide."}); return
                body = self._body()
                mode = str(body.get("mode") or "").strip().lower()
                victory = bool(body.get("victory", False))
                match_id = str(body.get("match_id") or "").strip()
                if mode not in ("classic", "ranked"):
                    self._json(400, {"ok": False, "error": "Mode multijoueur invalide."}); return
                if len(match_id) < 8 or len(match_id) > 160:
                    self._json(400, {"ok": False, "error": "Identifiant de match invalide."}); return

                chain_session_id = _safe_session_id(body.get("chain_session_id"))
                opponent_key = str(body.get("opponent_key") or "").strip().casefold()[:96]
                clean_completed = bool(body.get("clean_completed", False))
                abandoned = bool(body.get("abandoned", False))
                try:
                    duration_seconds = max(0.0, float(body.get("duration_seconds") or 0.0))
                    turns = max(0, int(body.get("turns") or 0))
                except Exception:
                    duration_seconds = 0.0
                    turns = 0

                conn = base.db()
                try:
                    _ensure_win_schema(conn)
                    conn.execute("BEGIN IMMEDIATE")
                    prior = conn.execute(
                        "SELECT * FROM multiplayer_reward_events WHERE event_id=? AND account_id=?",
                        (match_id, acc["account_id"]),
                    ).fetchone()
                    if prior:
                        conn.rollback()
                        stats = base.profile_stats_payload(conn, acc["account_id"])
                        state = base.economy_state(conn, acc["account_id"], False)
                        self._json(200, {
                            "ok": True,
                            "duplicate": True,
                            "reward": int(prior["reward"] or 0),
                            "streak": int(prior["streak_after"] or 0),
                            "profile_stats": stats,
                            "state": state,
                        }); return

                    deck_stars, _deck_ids = _deck_context(conn, acc["account_id"], body.get("player_deck") if "player_deck" in body else None)
                    quarters, prior_pair_count = _anti_farm_quarters(conn, acc["account_id"], mode, opponent_key)
                    current = _get_streak(conn, acc["account_id"], mode, chain_session_id)
                    bonus_bp = 0
                    base_amount = 0
                    advanced = False

                    if victory:
                        # Repeated-opponent games after the 3rd can still pay the reduced anti-farm reward,
                        # but cannot be used to artificially build the streak.
                        if quarters == 4:
                            streak = _set_streak(conn, acc["account_id"], mode, chain_session_id, current + 1)
                            advanced = True
                        else:
                            streak = current
                        reward, base_amount, bonus_bp = _winner_reward(mode, max(1, streak), deck_stars, quarters)
                    else:
                        streak = _set_streak(conn, acc["account_id"], mode, chain_session_id, 0)
                        valid_clean_loss = clean_completed and not abandoned and duration_seconds >= MIN_CLEAN_SECONDS and turns >= MIN_CLEAN_TURNS
                        reward = _loss_reward(quarters) if valid_clean_loss else 0

                    profile_event_id = hashlib.sha256((match_id + "|" + acc["account_id"]).encode("utf-8")).hexdigest()
                    stats, _profile_duplicate = base.record_profile_multiplayer(conn, acc["account_id"], mode, victory, profile_event_id)

                    meta = {
                        "match_id": match_id,
                        "mode": mode,
                        "victory": victory,
                        "streak": streak,
                        "deck_stars": deck_stars,
                        "star_bonus_bp": bonus_bp,
                        "win_chain_base": base_amount,
                        "anti_farm_quarters": quarters,
                        "pair_matches_in_window_before": prior_pair_count,
                        "clean_completed": clean_completed,
                        "abandoned": abandoned,
                        "duration_seconds": duration_seconds,
                        "turns": turns,
                    }
                    _credit_account(conn, acc["account_id"], reward, "match_win" if victory else "match_loss_complete", meta)
                    conn.execute(
                        """INSERT INTO multiplayer_reward_events(event_id,account_id,mode,victory,reward,player_stars,opponent_key,anti_farm_quarters,streak_after,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (match_id, acc["account_id"], mode, 1 if victory else 0, reward, deck_stars, opponent_key, quarters, streak, base.now()),
                    )
                    if opponent_key and (victory or clean_completed):
                        _record_opponent(conn, acc["account_id"], mode, opponent_key)
                    conn.commit()
                    state = base.economy_state(conn, acc["account_id"], False)
                except ValueError as exc:
                    conn.rollback()
                    self._json(400, {"ok": False, "error": str(exc)}); return
                finally:
                    conn.close()

                message = _reward_message(mode, victory, reward, streak, deck_stars, bonus_bp, quarters, advanced)
                self._json(200, {
                    "ok": True,
                    "duplicate": False,
                    "reward": reward,
                    "streak": streak,
                    "streak_advanced": advanced,
                    "deck_stars": deck_stars,
                    "star_bonus_bp": bonus_bp,
                    "win_chain_base": base_amount,
                    "anti_farm_quarters": quarters,
                    "anti_farm_percent": quarters * 25,
                    "message": message,
                    "profile_stats": stats,
                    "state": state,
                }); return

            return super().do_POST()
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    missing = []
    if not base.PUBLIC_BASE:
        missing.append("YUGITO_PUBLIC_BASE_URL (ou RENDER_EXTERNAL_URL)")
    if not base.GOOGLE_CLIENT_ID:
        missing.append("GOOGLE_CLIENT_ID")
    if not base.GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_CLIENT_SECRET")
    if base.IS_RENDER and not base.DATABASE_URL:
        missing.append("DATABASE_URL (PostgreSQL persistant)")
    if missing:
        print("ERREUR: variables d'environnement manquantes : " + ", ".join(missing))
        raise SystemExit(2)

    conn = base.db()
    try:
        _ensure_win_schema(conn)
        conn.commit()
    finally:
        conn.close()
    print(
        f"YUGITO Auth WIN_CHAIN_V1 sur 0.0.0.0:{base.PORT} -> {base.PUBLIC_BASE} "
        f"| DB={base.DB_BACKEND} | persistent={bool(base.DATABASE_URL)}"
    )
    base.ThreadingHTTPServer(("0.0.0.0", base.PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
