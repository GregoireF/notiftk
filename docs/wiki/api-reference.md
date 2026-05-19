# Référence API

**Base URL** : `https://notiftk.fly.dev`  
**Format** : JSON  
**Auth** : `X-API-Key: sk_live_xxx` (header) ou `?key=sk_live_xxx` (query param pour SSE)

---

## `GET /health`

Liveness check — ne touche pas TikTok. Idéal pour les health checks Fly.io et les monitors externes.

**Réponse 200 :**
```json
{
  "status": "ok",
  "version": "0.3.0",
  "auth_enabled": false,
  "cache_ttl_seconds": 30,
  "poll_interval_seconds": 5,
  "active_webhooks": 3
}
```

| Champ | Description |
|---|---|
| `auth_enabled` | `true` si `API_KEYS` ou `REQUIRE_API_KEY=true` |
| `cache_ttl_seconds` | Durée de cache côté serveur pour `/api/status` |
| `poll_interval_seconds` | Intervalle de polling TikTok pour SSE/webhooks |
| `active_webhooks` | Nombre de webhooks en cours d'écoute |

---

## `GET /api/status/{username}`

Vérification ponctuelle du statut live. Résultat mis en cache **30s** côté serveur.

**Auth :** `X-API-Key: sk_live_xxx`

**Paramètres :**
| Param | Type | Description |
|---|---|---|
| `username` | path | Pseudo TikTok (sans @), 1-24 chars `[a-zA-Z0-9._]` |

**Réponse 200 — en live :**
```json
{
  "username": "ninja",
  "is_live": true,
  "room_id": "7496121315238087466",
  "viewer_count": 12500,
  "title": "Fortnite ranked grind"
}
```

**Réponse 200 — hors ligne :**
```json
{
  "username": "ninja",
  "is_live": false,
  "room_id": null,
  "viewer_count": null,
  "title": null
}
```

**Cas spécial — stream 18+ :**
```json
{
  "username": "...",
  "is_live": true,
  "room_id": "749...",
  "viewer_count": null,
  "title": null
}
```
TikTok refuse les détails sans session authentifiée. `room_id` est renseigné mais `viewer_count` et `title` sont `null`.

**Codes d'erreur :**
| Code | Cas |
|------|-----|
| `200` | Succès (live ou offline) |
| `401` | Clé API manquante ou invalide |
| `404` | Utilisateur inexistant ou jamais allé en live |
| `422` | Username invalide (caractères interdits ou > 24 chars) |
| `429` | Rate limit dépassé (120 req/60s par IP/clé) |
| `502` | Erreur TikTok (timeout, API indisponible) |

```bash
# Sans auth
curl https://notiftk.fly.dev/api/status/ninja

# Avec auth
curl -H "X-API-Key: sk_live_abc123" https://notiftk.fly.dev/api/status/ninja
```

---

## `GET /api/stream/{username}`

Server-Sent Events — **change-only** : un événement est émis uniquement quand `is_live` change + un commentaire `: heartbeat` toutes les 30s pour maintenir la connexion.

**Auth :** `?key=sk_live_xxx` (EventSource ne supporte pas les headers HTTP)

**Format de la réponse :**
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no        ← désactive le buffering nginx/Fly.io
```

**Événements émis :**
```
# Statut initial + à chaque transition is_live
data: {"username":"ninja","is_live":true,"room_id":"...","viewer_count":12500,"title":"..."}

data: {"username":"ninja","is_live":false,"room_id":null,"viewer_count":null,"title":null}

# Erreur temporaire — continuer à écouter, SSE se reconnecte seul
data: {"username":"ninja","error":"TikTok API timed out","transient":true}

# Erreur fatale — fermer la connexion
data: {"username":"ghost","error":"User not found"}

# Keepalive (commentaire SSE, ignoré par EventSource)
: heartbeat
```

**Logique de reconnexion :**
- `transient: true` → erreur TikTok passagère, garder la connexion ouverte
- Pas de `transient` → erreur permanente (user inexistant), appeler `src.close()`
- Erreur réseau → EventSource se reconnecte automatiquement

```bash
# Écouter le stream (Ctrl+C pour arrêter)
curl -N "https://notiftk.fly.dev/api/stream/ninja?key=sk_live_..."
```

---

## `GET /api/stream?users=u1,u2,u3`

SSE multi-usernames — une seule connexion pour surveiller jusqu'à **10 streamers** simultanément. Chaque événement inclut `username` pour identifier la source.

**Paramètres :**
| Param | Type | Description |
|---|---|---|
| `users` | query | Pseudos séparés par des virgules, max 10 |

```
data: {"username":"ninja","is_live":true,...}
data: {"username":"pokimane","is_live":false,...}
data: {"username":"shroud","is_live":true,...}
: heartbeat
```

```bash
curl -N "https://notiftk.fly.dev/api/stream?users=ninja,pokimane,shroud&key=sk_live_..."
```

---

## `POST /api/keys`

Génère une nouvelle clé API en self-service. La clé brute est retournée **une seule fois**.

**Auth :** aucune (public)  
**Rate limit :** 3 clés par IP toutes les 24h

**Query params optionnels :**
| Param | Type | Description |
|---|---|---|
| `label` | string | Étiquette mémo pour identifier la clé |

**Réponse 201 :**
```json
{
  "id": "a1b2c3d4",
  "key": "sk_live_...",
  "warning": "Copiez cette clé maintenant — elle ne sera plus affichée."
}
```

**Codes d'erreur :**
| Code | Cas |
|------|-----|
| `201` | Clé générée |
| `429` | 3 clés/IP/24h atteintes, ou cap global (`MAX_KEYS`) dépassé |

```bash
# Génération simple
curl -X POST https://notiftk.fly.dev/api/keys

# Avec label
curl -X POST "https://notiftk.fly.dev/api/keys?label=mon-bot-discord"
```

---

## `POST /api/watch`

Enregistre un webhook. NotiTFK POSTera à l'URL donnée à chaque changement `is_live`.

**Auth :** `X-API-Key: sk_live_xxx`

**Corps (JSON) :**
```json
{
  "username": "ninja",
  "callback_url": "https://ton-bot.example.com/notiftk-hook",
  "secret": "optionnel-pour-hmac"
}
```

| Champ | Requis | Description |
|---|---|---|
| `username` | ✅ | Pseudo TikTok à surveiller |
| `callback_url` | ✅ | URL HTTPS publique qui recevra les POST |
| `secret` | ❌ | Si fourni, active la signature `X-NotiTFK-Signature: sha256=<hex>` |

**Payload envoyé à votre callback :**
```json
{
  "username": "ninja",
  "is_live": true,
  "room_id": "7496121315238087466",
  "viewer_count": 12500,
  "title": "Fortnite ranked grind"
}
```

**Réponse 201 :**
```json
{
  "watch_id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "ninja",
  "callback_url": "https://ton-bot.example.com/notiftk-hook"
}
```

**Codes d'erreur :**
| Code | Cas |
|------|-----|
| `201` | Webhook créé |
| `401` | Clé API invalide |
| `422` | URL invalide, SSRF détecté, ou username invalide |
| `429` | Limite atteinte (5/username ou 100 global) |

**Auto-suppression :** un webhook est automatiquement révoqué après 5 échecs de livraison consécutifs, ou si l'utilisateur TikTok n'existe plus.

---

## `DELETE /api/watch/{watch_id}`

Supprime un webhook enregistré.

**Auth :** `X-API-Key: sk_live_xxx`

| Code | Cas |
|------|-----|
| `204` | Webhook supprimé |
| `404` | `watch_id` introuvable |

```bash
curl -X DELETE \
  -H "X-API-Key: sk_live_..." \
  https://notiftk.fly.dev/api/watch/550e8400-e29b-41d4-a716-446655440000
```

---

## `GET /api/watches`

Liste tous les webhooks actifs. Les secrets ne sont jamais exposés.

**Auth :** `X-API-Key: sk_live_xxx`

**Réponse 200 :**
```json
[
  {
    "watch_id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "ninja",
    "callback_url": "https://ton-bot.example.com/notiftk-hook"
  }
]
```

---

## `GET /api/admin/keys` *(admin)*

Liste toutes les clés API (actives et révoquées).

**Auth :** `X-Admin-Secret: <votre-secret>`

> Retourne **404** si `ADMIN_SECRET` n'est pas configuré.

**Réponse 200 :**
```json
[
  { "id": "a1b2c3d4", "label": "mon-bot", "created_at": 1716000000.0, "is_active": true },
  { "id": "e5f6g7h8", "label": null, "created_at": 1715900000.0, "is_active": false }
]
```

```bash
curl -H "X-Admin-Secret: votre-secret" https://notiftk.fly.dev/api/admin/keys
```

---

## `DELETE /api/admin/keys/{key_id}` *(admin)*

Révoque une clé API. La clé devient immédiatement invalide.

**Auth :** `X-Admin-Secret: <votre-secret>`

| Code | Cas |
|------|-----|
| `204` | Clé révoquée |
| `404` | `key_id` introuvable ou `ADMIN_SECRET` non configuré |

```bash
curl -X DELETE \
  -H "X-Admin-Secret: votre-secret" \
  https://notiftk.fly.dev/api/admin/keys/a1b2c3d4
```
