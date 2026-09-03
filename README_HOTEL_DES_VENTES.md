# Hôtel des ventes YUGITO GC — serveur 2.1.0

Cette version ajoute un marché joueur-à-joueur fondé sur des exemplaires de cartes uniques.

## Garanties

- achat et transfert de propriété atomiques ;
- débit de l'acheteur et crédit intégral du vendeur dans la même transaction ;
- impossibilité d'acheter sa propre annonce ;
- une seule annonce active par exemplaire ;
- retrait immédiat de l'exemplaire des cartes jouables tant que l'annonce reste active ;
- conservation du Potentiel et des bonus d'Éveil ;
- prix libre de 1 à 10 000 000 YT ;
- 30 annonces actives maximum par vendeur ;
- historique vendeur/acheteur dans `economy_ledger` ;
- migration automatique et idempotente des anciennes possessions vers `card_instances`.

Il n'y a actuellement aucune taxe de vente.

## API authentifiée

- `GET /api/market/listings` — marché, filtres cumulables `search` (nom ou identifiant de carte), `min_percent` (Potentiel + Perfection, de 90 à 110), `max_price`, `full_only`, ainsi que `card_id`, `min_price`, `min_potential`, `sort` et `limit` ;
- `GET /api/market/mine` — annonces actives du joueur ;
- `GET /api/market/history` — ventes, achats et annulations ;
- `POST /api/market/list` — `{instance_id, price_yt}` ;
- `POST /api/market/update-price` — `{listing_id, price_yt}` ;
- `POST /api/market/cancel` — `{listing_id}` ;
- `POST /api/market/buy` — `{listing_id}`.

Le déploiement Render reste identique (`python yugito_auth_server.py`). PostgreSQL est obligatoire sur Render. La migration s'exécute au premier démarrage sans supprimer `owned_cards`, afin de conserver une voie de retour et la compatibilité historique.
