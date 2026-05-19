# Alternatives à Fly.io

> La plupart de ces plateformes ne proposent pas de stockage persistant en tier gratuit — les webhooks et clés self-service sont perdus au redémarrage. Pour un usage avec persistance, utilisez [Fly.io](flyio.md).

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

## Oracle Cloud Always Free

**Avantages** : 2 VMs ARM 4 cœurs / 24 GB RAM au total, **stockage bloc persistant**, sans carte bancaire (compte Oracle requis).  
**Inconvénient** : inscription parfois refusée selon la région ; setup plus complexe qu'une PaaS.

```bash
# Sur la VM Oracle (Ubuntu 22.04 ARM)
sudo apt update && sudo apt install -y python3-pip python3-venv git

git clone https://github.com/votre-user/notiftk && cd notiftk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Créer un service systemd
cat > /etc/systemd/system/notiftk.service << 'EOF'
[Unit]
Description=NotiTFK API
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/notiftk
ExecStart=/home/ubuntu/notiftk/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8080
Restart=always
Environment=WEBHOOKS_DB=/data/notiftk/webhooks.db
Environment=API_KEYS=sk_live_abc123
Environment=ADMIN_SECRET=votre-secret
StandardOutput=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now notiftk
```

HTTPS via [Caddy](https://caddyserver.com) (reverse-proxy automatique) :
```
# /etc/caddy/Caddyfile
notiftk.example.com {
    reverse_proxy localhost:8080
}
```

**Persistance** : monter le volume bloc Oracle sur `/data` — les webhooks survivent aux redémarrages.

---

## Render

**Avantages** : Free tier avec 750h/mois, HTTPS auto, sans carte bancaire.  
**Inconvénient** : spin-down après 15min d'inactivité — coupe toutes les connexions SSE actives.

```
Build command : pip install -r requirements.txt
Start command : uvicorn api.main:app --host 0.0.0.0 --port 10000
```

> Le spin-down est rédhibitoire pour les connexions SSE longues. Render convient uniquement pour l'endpoint REST `/api/status/{username}`.

---

## Docker (auto-hébergé)

Pour un déploiement sur VPS ou machine locale. Le `Dockerfile` est inclus dans le dépôt.

```bash
# Build
docker build -t notiftk .

# Run avec volume persistant (port hôte:8000 → container:8080)
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

Démarrer :
```bash
# Copier les variables
cp .env.example .env  # éditer avec vos valeurs
docker compose up -d
```

---

## Railway

**Avantages** : 5 $/mois de crédits (hobby plan, ~500h runtime), HTTPS auto.  
**Inconvénient** : nécessite une carte bancaire ; pas de persistance disque gratuite.

```bash
railway login
railway init
railway up
```

Variables via l'UI Railway ou `.railway.toml`.

---

## Comparatif

| | Fly.io | Koyeb | Oracle Cloud | Render | Docker VPS |
|---|---|---|---|---|---|
| Toujours actif | ✅ | ✅ | ✅ | ❌ spin-down | ✅ |
| HTTPS auto | ✅ | ✅ | via Caddy | ✅ | via Caddy |
| Volume persistant | ✅ 3 GB | ❌ | ✅ illimité | ❌ | ✅ illimité |
| Webhooks persistants | ✅ | ❌ | ✅ | ❌ | ✅ |
| SSE longue durée | ✅ | ✅ | ✅ | ❌ | ✅ |
| Carte bancaire | Non | Non | Non | Non | Oui (VPS) |
| Coût mensuel | 0 € | 0 € | 0 € | 0 € | ~4-6 €/mois |
| Complexité setup | Faible | Faible | Élevée | Faible | Moyenne |

**Recommandation** : Fly.io pour un déploiement rapide avec persistance. Oracle Cloud si vous voulez une VM dédiée gratuite et êtes à l'aise avec Linux.
