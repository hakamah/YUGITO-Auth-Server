# YUGITO Auth Server 2.3.2 — Android Google Web Bootstrap

## Objectif
Supprimer l'attente bloquante de `/api/device/start` avant l'ouverture de Google sur Android.

## Nouveau flux Android V12
1. L'APK génère localement un `device_code` aléatoire à usage unique.
2. Elle ouvre immédiatement dans un Custom Tab :
   `/login/start?device_code=<code>&platform=android`
3. Le serveur crée/rafraîchit la ligne `devices` pour ce code puis redirige vers `/login?device_code=<code>`.
4. `/login` redirige vers la page OAuth officielle Google avec `prompt=select_account`.
5. Après validation Google, `/oauth/callback` complète la session YUGITO et renvoie vers l'app Android.
6. L'APK poll `/api/device/status?device_code=<code>` et récupère la session YUGITO.

Les anciens flux `/api/device/start` et `/api/device/google-token` restent disponibles.
