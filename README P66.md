YUGITO AUTH SERVER P66 — ECONOMY / COLLECTION / GODOT

Base: current YUGITO-Auth-Server-main supplied by the user.

ALREADY PRESENT IN CURRENT SERVER
- PostgreSQL economy via yugito_economy.py.
- Server-persisted weekly rotations.
- Weekly counts: 8x 3.5★, 6x 4★, 4x 4.5★, 4x 5★.
- Rotation: Monday 00:00 Europe/Paris.
- Preference for cards not free during the previous week.
- Permanent ownership in yugito_owned_cards.
- GET /api/economy/state returns:
  yt_balance, owned_card_ids, base_card_ids, free_card_ids,
  available_card_ids, rotation, penalty, catalog.
- POST /api/economy/purchase already exists.
- Deck validation, match permits and match settlement already exist.
- Abandon penalty already exists and 3 clean matches reduce one penalty tier.

P66 CHANGES
- Canonical prices:
  3★=0, 3.5★=500, 4★=1000, 4.5★=1500, 5★=2000.
- Classic: winner +30 YT, natural loser +10 YT.
- Ranked: winner +30 YT, natural loser +10 YT.
- Abandon/disconnect loser: 0 YT.
- Same-pair anti-farm over 6 hours:
  matches 1-3 = 100%, 4th = 50%, 5th = 25%, 6th+ = 0%.
- GET /api/collection/weekly added. It returns the exact authoritative card IDs.
- GET /api/economy/state remains the authenticated authority for balance and ownership.

NEW CARDS
No total-card-count constant is used for the rotation.
A new card only needs to be added to card_catalog.json with its id/stars/price.
It then automatically joins the pool for its rarity.

RENDER
Keep all existing environment variables.
Verify:
- YUGITO_DATABASE_URL (or DATABASE_URL)
- YUGITO_ROTATION_SECRET
Never commit secrets to GitHub.
