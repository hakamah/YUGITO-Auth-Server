# YUGITO Auth Server 2.2.2 — Profil joueur

Ajouts :
- `profile_stats` dans `/api/economy/state` ;
- `/api/profile/stats` ;
- suivi des parties Solo terminées via `solo_permits` ;
- compteur d'exemplaires permanents ;
- compteur de cartes FULL 110 % (Potentiel 100 % +10 % Perfection) ;
- statistiques Multi Classic / Classé : victoires, défaites, parties, winrate ;
- `/api/profile/multiplayer/record` idempotent pour les futurs règlements de duel ;
- migration best-effort des anciens résultats `yugito_matches` lorsqu'ils existent déjà.

Déploiement : remplacer les fichiers du dépôt GitHub YUGITO-Auth-Server par le contenu de ce ZIP puis laisser Render redéployer.
Aucune variable Render supplémentaire n'est requise.
