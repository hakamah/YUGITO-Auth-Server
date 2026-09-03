# YUGITO Auth Server 2.3.1 — Google natif Android

Base: 2.2.2 Profile Stats + économie/instances/progression/HDV.

Ajouts :
- `POST /api/device/start` renvoie aussi `google_client_id` et `native_google=true`.
- `POST /api/device/google-token` accepte un ID token Google natif et finalise le même device flow/session YUGITO que le navigateur historique.
- mode `credential_manager` : validation audience/issuer/expiration + nonce lié au `device_code`.
- mode `account_manager` (compatibilité Android sans dépendance embarquée) : validation audience/issuer/expiration ; liaison par `device_code` court et à usage unique.
- `google-auth` est utilisé en production pour vérifier le jeton Google.
- le flux OAuth navigateur existant reste présent comme secours.

Aucune migration des comptes YUGITO n'est nécessaire : `google_sub` reste la clé d'identité Google et les mêmes `account_id`, pseudo, YT, ELO, cartes et statistiques sont conservés.

Déploiement Render : conserver les variables existantes `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `DATABASE_URL` et l'URL publique. Commande : `python yugito_auth_server.py`.
