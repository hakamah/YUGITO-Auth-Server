# YUGITO Auth Server 2.2.1 — production CardInstance / progression / HDV

Cette version part du serveur 2.1.0 Hôtel des ventes et rend la progression autoritaire côté serveur.

## Routes authentifiées ajoutées

- `POST /api/economy/train` — `{instance_id}` — 100 YT, Potentiel <100, 62 % réussite / 38 % échec.
- `POST /api/economy/perfect` — `{instance_id, stat}` — 200 YT, Potentiel 100 %. Premier +1 sur la statistique : 62/38. À partir de +1 : 44 % réussite / 28 % échec / 28 % Blessure. Plafond +10 total.
- `POST /api/economy/change-art` — `{instance_id, source_stat, target_stat}` — 200 YT à +10 total. 44 % transfert / 28 % échec / 28 % Blessure (perte du point source).

Tous les débits YT, tirages RNG et mutations CardInstance sont exécutés dans la transaction serveur. Une carte en vente ne peut pas être entraînée.

Le HDV 2.1.0 reste inchangé : propriété, achat, vente, annulation et historique sont atomiques côté serveur.

## Déploiement

Conserver les variables Render existantes (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `DATABASE_URL`, URL publique) et utiliser `python yugito_auth_server.py`. PostgreSQL reste obligatoire sur Render.
