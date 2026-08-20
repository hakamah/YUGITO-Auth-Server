YUGITO AUTH SERVER 1.1.8 — DEPLOIEMENT

BUT
Le serveur Render Free utilise un disque éphémère. La version 1.1.8 ne dépend donc
plus de SQLite pour conserver durablement l'identité :
  1) les sessions YUGITO sont signées (survivent aux redémarrages Render),
  2) account_id + pseudo + ELO sont sauvegardés dans le dossier appData Google Drive
     du compte Google, accessible uniquement à YUGITO.

A FAIRE UNE SEULE FOIS DANS GOOGLE CLOUD
1. Ouvre le projet Google Cloud qui contient GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.
2. APIs & Services > Library.
3. Active "Google Drive API".
4. Ne change pas l'URI de redirection :
   https://yugito-auth-server.onrender.com/oauth/callback

DEPLOIEMENT RENDER
1. Remplace yugito_auth_server.py dans le repo YUGITO-Auth-Server par celui de ce pack.
2. requirements.txt peut être remplacé également (paho-mqtt + gunicorn).
3. Commit/push sur main et laisse Render redéployer.
4. Teste :
   https://yugito-auth-server.onrender.com/health
   La réponse doit contenir :
     "version":"1.1.8"
     "drive_identity":true
     "stateless_sessions":true

VARIABLES RENDER
Les variables existantes restent suffisantes :
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- YUGITO_PUBLIC_BASE_URL (facultatif si RENDER_EXTERNAL_URL est correct)

Optionnel :
- YUGITO_SESSION_SECRET
  Si absent, GOOGLE_CLIENT_SECRET sert aussi à signer les sessions YUGITO.

MIGRATION
La première connexion 1.1.8 depuis un PC/Android qui connaissait déjà Hakamah
restaure silencieusement ce pseudo puis crée le fichier appData Google. À partir de
là, un autre appareil utilisant le même Google récupère Hakamah automatiquement.

IMPORTANT
Le formulaire de connexion Google lui-même ne doit pas être intégré dans une WebView
contrôlée par YUGITO : Google interdit les user-agents OAuth embarqués. Le client
reste sur son écran YUGITO pendant l'attente, ouvre uniquement la fenêtre Google
sécurisée du système, puis reprend automatiquement après validation.
