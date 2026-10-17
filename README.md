# EdenEverywhere cloud master

The always-on hub for the EdenEverywhere collective. Every phone runs its
own server for offline/LAN sharing; this master aggregates everything when
phones have internet. Same JSON API as the Android server, so the app talks
to both identically.

## Run on your own computer

```bash
python3 server.py
# listens on http://localhost:8080, stores data in ./eden.db
```

## Deploy on Render (Starter ~$7/mo)

1. Push this folder to a GitHub repo.
2. In Render: New → Blueprint → point at the repo (uses `render.yaml`).
3. Done. Your master URL will be `https://edeneverywhere-cloud.onrender.com`.

Note: Render's free tier doesn't support persistent disks, so this uses the Starter plan to keep your map data safe across restarts.

## Deploy on Fly.io

```bash
fly launch   # accept the Dockerfile
fly volumes create edendata --size 1
fly deploy
```

## Point the app at it

In EdenEverywhere: ⚙️ → **Cloud master** → paste your URL →
everything the collective creates funnels up to it, and every phone pulls
the full shared map from it.
