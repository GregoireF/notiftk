# Architecture & Décisions techniques

---

## Flux de détection TikTok

TikTok n'a pas d'API publique pour le statut live. NotiTFK passe par [TikTokLive](https://github.com/isaackogan/TikTokLive), qui utilise les mêmes endpoints internes que le navigateur.

```
Client HTTP          NotiTFK                      TikTok
    │                   │                            │
    │ GET /api/status   │                            │
    │──────────────────►│                            │
    │                   │ fetch_room_id(username)    │
    │                   │───────────────────────────►│
    │                   │◄── room_id ────────────────│
    │                   │ fetch_room_info(room_id)   │
    │                   │───────────────────────────►│
    │                   │◄── { is_live, title, ... } │
    │                   │                            │
    │◄── JSON ──────────│ (mis en cache 30s)         │
```

### Cache à deux niveaux

```
REST  → Cache 30s   (bots one-shot — évite de spammer TikTok)
SSE   → Cache 5s    (partagé entre N subscribers → 1 appel/5s par username)
```

Si 50 clients SSE regardent "ninja", NotiTFK ne fait qu'**1 appel TikTok toutes les 5s**, pas 50.

---

## Pourquoi TikTokLive et pas une implémentation custom ?

Le `msToken` TikTok anti-bot est généré par du JavaScript obfusqué, mis à jour régulièrement. Sans lui, toutes les requêtes retournent `{}`. TikTokLive est la bibliothèque qui maintient ce reverse-engineering — notre rôle est de garder `requirements.txt` à jour via Dependabot.

**Stratégie de résilience :**
1. Dependabot ouvre une PR chaque lundi si une nouvelle version sort
2. Le test d'intégration (`pytest -m integration`) valide que TikTok répond correctement
3. Si TikTok change son API, `pip install TikTokLive --upgrade` suffit dans 90% des cas

---

## SSE — change-only events

NotiTFK n'envoie **pas** un événement à chaque poll. Il compare l'état actuel avec l'état précédent :

```python
# Pseudo-code simplifié
prev = None
while True:
    current = await get_live_status(username)
    if current.is_live != prev:
        yield current     # ← événement SSE émis
        prev = current.is_live
    await asyncio.sleep(POLL_INTERVAL)  # 5s
    yield ": heartbeat\n\n"             # toutes les 30s
```

**Impact** : pour un streamer qui fait 4h de live avec poll à 5s → **2 événements** (début + fin) au lieu de **2880** (1 par poll). Bande passante réduite ×1440.

---

## Webhooks — architecture

```
register_webhook()
    → SQLite: INSERT INTO webhooks
    → asyncio.Task: _watch_loop(watcher)
        → while True:
            get_live_status_sse(username)  # partagé avec SSE
            if is_live != prev:
                _dispatch(watcher, status)   # POST callback_url
                if failures >= 5: unregister()
```

**Persistance** : les webhooks survivent aux redémarrages. Au démarrage de l'app (`lifespan`), `restore_webhooks()` relit SQLite et relance les tâches asyncio.

**SSRF prevention** : les URL de callback sont validées contre une blocklist de plages IP privées avant insertion.

---

## Authentification — double source

```python
# api/auth.py
if key in VALID_KEYS:           # clés env (API_KEYS)
    return key
if is_db_key_valid(key):        # clés self-service (SQLite)
    return key
raise HTTPException(401)
```

Les deux types sont équivalents côté validation. Les clés DB sont stockées hashées (SHA-256) — la clé brute n'est jamais persistée.

---

## Structure des fichiers

```
api/
├── __init__.py
├── main.py        FastAPI app, endpoints, lifespan, SSE generators
├── tiktok.py      TikTokLive wrapper, cache, erreurs
├── webhooks.py    SQLite persistence, SSRF check, dispatch HMAC, poll loop
├── auth.py        Rate limiting (sliding window), API key validation
├── keys.py        Self-service key generation, SHA-256 hashing, per-IP limits
└── models.py      Pydantic models (LiveStatus, WatchRequest, WatchResponse, ErrorResponse)
```

---

## Choix technologiques

### FastAPI vs Flask vs Django

| | FastAPI | Flask | Django |
|---|---|---|---|
| SSE async natif | ✅ `StreamingResponse` | ⚠️ complex | ❌ |
| Pydantic validation | ✅ natif | ❌ | ❌ |
| Auto-docs (Swagger) | ✅ natif | ❌ | ❌ |
| Async webhooks | ✅ `asyncio.Task` | ⚠️ threads | ❌ |

### Python vs Node.js vs Go

| | Python | Node.js | Go |
|---|---|---|---|
| TikTokLive mature | ✅ | ⚠️ partiel | ❌ |
| SSE async natif | ✅ FastAPI | ✅ | ✅ |
| Maintenabilité | ✅ | ✅ | Moyenne |

Python reste le bon choix tant que TikTokLive (Python) est la référence.

### SQLite vs PostgreSQL

SQLite est suffisant pour ce cas d'usage :
- Webhooks = quelques centaines de lignes max
- Clés API = quelques milliers
- Accès mono-process sur Fly.io (1 machine)
- Volume persistent Fly.io = pas de perte de données
- Pas de frais supplémentaires

Migrer vers PostgreSQL n'apporterait de valeur qu'en multi-instance avec écritures concurrentes.

---

## Scalabilité

```
Stage 1 — Actuel (0 €/mois)
  1 instance Fly.io 256 MB
  Cache RAM (dict Python)
  ~100 connexions SSE simultanées
  ~50 webhooks actifs

Stage 2 — Multi-instance (5-10 €/mois)
  N instances Fly.io
  Upstash Redis (free tier) pour cache partagé
  → évite N appels TikTok/username/5s
  Limites par IP en Redis plutôt qu'en mémoire

Stage 3 — SaaS (50 €+/mois)
  Auto-scaling Fly.io
  Redis Pub/Sub : 1 worker TikTok/username, N instances diffusent aux clients SSE
  PostgreSQL : clés API + analytics + billing
  Sentry : alertes d'erreurs
  Prometheus : métriques temps réel
```

---

## Infrastructure as Code

L'app Fly.io est gérée via OpenTofu (≥ 1.9) avec un backend HCP Terraform :

```
infra/flyio/
├── main.tf        App fly_app.this, import notiftk
├── variables.tf   fly_org
├── outputs.tf     app_hostname (notiftk.fly.dev)
├── providers.tf   fly-apps/fly ~> 0.0
└── terraform.tf   backend HCP Terraform (workspace notifk-flyio)
```

Les machines (compute) sont créées par `fly deploy` en CI — séparation stricte infra / déploiement.
