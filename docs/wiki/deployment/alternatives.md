# Alternatives à Fly.io

> Ces plateformes ne supportent pas le stockage persistant en tier gratuit — les webhooks et clés self-service sont perdus au redémarrage. Pour un usage avec persistance, utilisez [Fly.io](flyio.md).

---

## Koyeb

**Avantages** : 0,1 vCPU / 512 MB, toujours actif, sans carte bancaire.  
**Inconvénient** : pas de stockage disque persistant en gratuit.

```
# Déploiement via l'UI Koyeb :
# https://app.koyeb.com → New App → GitHub → sélectionner le repo

Build command : pip install -r requirements.txt
Run command   : uvicorn api.main:app --host 0.0.0.0 --port 8000
Port          : 8000
```

Variables d'environnement à configurer :
```
API_KEYS=sk_live_abc123
ADMIN_SECRET=votre-secret
REQUIRE_API_KEY=true
```

**Note** : sans `WEBHOOKS_DB` pointant sur un volume persistant, les webhooks et clés sont stockés en mémoire et perdus au redémarrage.

---

## Railway

**Avantages** : 5 $/mois de crédits (hobby plan, ~500h runtime).  
**Inconvénient** : pas de disque persistant gratuit, nécessite une CB.

```bash
railway login
railway init
railway up
```

Variables via l'UI Railway ou `.railway.toml`.

---

## Render

**Avantages** : Free tier avec 750h/mois, HTTPS auto.  
**Inconvénient** : spin-down après 15min d'inactivité (Free), problématique pour SSE.

```
Build command : pip install -r requirements.txt
Start command : uvicorn api.main:app --host 0.0.0.0 --port 10000
```

> Le spin-down coupe toutes les connexions SSE — non recommandé pour ce cas d'usage.

---

## Docker (auto-hébergé)

Pour un déploiement sur VPS (Hetzner, OVH, etc.) :

```bash
# Build
docker build -t notiftk .

# Run avec volume persistant
docker run -d \
  -p 8000:8080 \
  -v notiftk_data:/data \
  -e API_KEYS=sk_live_abc123 \
  -e ADMIN_SECRET=votre-secret \
  -e WEBHOOKS_DB=/data/webhooks.db \
  --name notiftk \
  notiftk
```

Avec docker-compose :

```yaml
version: '3.8'
services:
  notiftk:
    build: .
    ports:
      - "8000:8080"
    volumes:
      - notiftk_data:/data
    environment:
      - API_KEYS=${API_KEYS}
      - ADMIN_SECRET=${ADMIN_SECRET}
      - WEBHOOKS_DB=/data/webhooks.db
    restart: unless-stopped

volumes:
  notiftk_data:
```

---

## Comparatif

| | Fly.io | Koyeb | Railway | Render | Docker VPS |
|---|---|---|---|---|---|
| Toujours actif | ✅ | ✅ | ✅ | ❌ spin-down | ✅ |
| HTTPS auto | ✅ | ✅ | ✅ | ✅ | via Caddy/nginx |
| Volume persistant | ✅ 3 GB | ❌ | ❌ | ❌ | ✅ illimité |
| Webhooks persistants | ✅ | ❌ | ❌ | ❌ | ✅ |
| SSE longue durée | ✅ | ✅ | ✅ | ❌ | ✅ |
| Carte bancaire | Non | Non | Oui | Non | Oui (VPS) |
| Coût mensuel | 0 € | 0 € | ~0 € | 0 € | ~4-6 €/mois |
