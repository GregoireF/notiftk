# Variables d'environnement — Référence complète

Toutes les variables NotiTFK avec leurs valeurs par défaut et leur impact.

---

## Authentification

| Variable | Défaut | Description |
|---|---|---|
| `API_KEYS` | *(vide)* | Clés valides séparées par des virgules. Vide = auth désactivée. |
| `REQUIRE_API_KEY` | `false` | `true` = auth requise même si `API_KEYS` est vide. |
| `ADMIN_SECRET` | *(vide)* | Secret pour `X-Admin-Secret` header (admin endpoints). Vide = endpoints retournent 404. |

```bash
export API_KEYS=sk_live_abc123,sk_live_xyz789
export REQUIRE_API_KEY=true
export ADMIN_SECRET=super-secret-fort-32chars
```

---

## Clés self-service

| Variable | Défaut | Description |
|---|---|---|
| `MAX_KEYS` | `1000` | Cap global de clés actives en base. |
| `MAX_KEYS_PER_IP` | `3` | Max clés générables par IP toutes les 24h. |

---

## Rate limiting

| Variable | Défaut | Description |
|---|---|---|
| `RATE_LIMIT_REQUESTS` | `120` | Max requêtes par fenêtre de temps. |
| `RATE_LIMIT_WINDOW` | `60` | Durée de la fenêtre en secondes. |

---

## Persistence

| Variable | Défaut | Description |
|---|---|---|
| `WEBHOOKS_DB` | `data/webhooks.db` | Chemin vers la base SQLite (webhooks + clés API). |

Sur Fly.io avec volume persistant :
```bash
fly secrets set WEBHOOKS_DB=/data/webhooks.db -a notiftk
```

---

## Webhooks

| Variable | Défaut | Description |
|---|---|---|
| `MAX_WEBHOOKS` | `100` | Cap global du nombre de webhooks actifs. |
| `MAX_WEBHOOKS_PER_USER` | `5` | Max webhooks pour un même username. |

---

## TikTok / Cache

Ces constantes ne sont pas exposées en env var — modifier directement dans `api/tiktok.py` si besoin :

| Constante | Valeur | Description |
|---|---|---|
| `CACHE_TTL` | `30` | Durée du cache REST en secondes. |
| `POLL_INTERVAL` | `5` | Intervalle de polling TikTok pour SSE/webhooks. |
| `DISPATCH_TIMEOUT` | `10` | Timeout des appels webhook (secondes). |
| `MAX_DISPATCH_FAILURES` | `5` | Échecs consécutifs avant suppression auto du webhook. |

---

## Exemple `.env` complet

```bash
# Auth
API_KEYS=sk_live_abc123,sk_live_xyz789
REQUIRE_API_KEY=true
ADMIN_SECRET=super-secret-fort-32chars

# Self-service keys
MAX_KEYS=1000
MAX_KEYS_PER_IP=3

# Rate limiting
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW=60

# Persistence
WEBHOOKS_DB=/data/webhooks.db

# Webhooks
MAX_WEBHOOKS=100
MAX_WEBHOOKS_PER_USER=5
```

> Ne jamais committer ce fichier — l'ajouter à `.gitignore`.
