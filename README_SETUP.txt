YUGITO — COMPTE GOOGLE UNIQUE PC + MOBILE
=========================================

FLUX OFFICIEL
-------------
1. Premier lancement PC ou Mobile -> bouton « SE CONNECTER AVEC GOOGLE ».
2. Le navigateur Google s'ouvre. YUGITO ne voit jamais le mot de passe Google.
3. Le serveur lit l'identifiant Google stable (sub).
4. Si ce Google possède déjà un compte YUGITO : retour immédiat du pseudo existant.
5. Si ce Google est nouveau : YUGITO demande « Choisis ton pseudo » UNE SEULE FOIS.
6. Le serveur enregistre Google sub -> account_id YUGITO -> pseudo -> ELO.
7. Sur n'importe quel autre PC/téléphone, le même Google recharge automatiquement le même compte.

MIGRATION D'UN ANCIEN COMPTE PC
-------------------------------
Le flux reste exactement le même : Google D'ABORD, pseudo ENSUITE.
Si le joueur choisit sur PC le même pseudo que son ancienne identité locale, le launcher joint
silencieusement une preuve de propriété. Le serveur vérifie le registre historique YUGITO et
reprend l'ancien account_id. Il n'y a donc PAS d'écran spécial « lier mon ancien compte ».
Sur Mobile, un pseudo historique déjà réservé ne peut pas être volé.

CONFIGURATION GOOGLE (UNE FOIS POUR LE PROJET)
-----------------------------------------------
1. Google Cloud Console -> nouveau projet « YUGITO ».
2. OAuth consent screen -> External -> nom YUGITO.
3. Credentials -> Create credentials -> OAuth client ID -> Web application.
4. Déployer CE dossier sur un serveur HTTPS (Render est préparé avec render.yaml).
5. Dans Render, définir :
   YUGITO_PUBLIC_BASE_URL=https://<ton-service>.onrender.com
   GOOGLE_CLIENT_ID=<client id Google>
   GOOGLE_CLIENT_SECRET=<secret Google>
6. Dans Google Cloud, ajouter l'URI de redirection :
   https://<ton-service>.onrender.com/oauth/callback
7. Mettre la même URL https://<ton-service>.onrender.com dans :
   - PC : google_auth_config.json
   - Mobile : assets/www/auth_config.json

IMPORTANT
---------
Le GOOGLE_CLIENT_SECRET reste UNIQUEMENT sur le serveur. Il ne doit jamais être intégré dans
l'EXE PC ni dans l'APK Android.

ENDPOINTS
---------
POST /api/device/start
GET  /login?device_code=...
GET  /oauth/callback
GET  /api/device/status?device_code=...
GET  /api/account/me
POST /api/account/claim-pseudo
POST /api/logout

La route /api/account/link-legacy reste uniquement pour compatibilité avec d'anciens clients.
