# Authentification & Rate Limiting

---

## Vue d'ensemble

NotiTFK supporte deux types de clés API — elles sont **équivalentes** côté validation :

| Type | Génération | Révocation | Usage |
|---|---|---|---|
| **Clé d'environnement** | `fly secrets set API_KEYS=sk_live_...` | Retirer de `API_KEYS` | Admin / usage propre |
| **Clé self-service** | `POST /api/keys` | `DELETE /api/admin/keys/{id}` | Utilisateurs externes |

---

## Utiliser une clé

### REST (header)
```bash
curl -H "X-API-Key: sk_live_abc123" https://notiftk.fly.dev/api/status/ninja
```

### SSE (query param)
EventSource ne supporte pas les headers HTTP — la clé passe en query param :
```bash
curl -N "https://notiftk.fly.dev/api/stream/ninja?key=sk_live_abc123"
```

```js
const src = new EventSource(`/api/stream/ninja?key=${apiKey}`);
```

### Webhooks
```bash
curl -X POST https://notiftk.fly.dev/api/watch \
  -H "X-API-Key: sk_live_abc123" \
  -H "Content-Type: application/json" \
  -d '{"username":"ninja","callback_url":"https://..."}'
```

---

## Clés self-service

### Générer une clé

```bash
curl -X POST https://notiftk.fly.dev/api/keys
# → { "id": "a1b2c3d4", "key": "sk_live_...", "warning": "..." }
```

- **Format** : `sk_live_<48 hex chars>` (192 bits d'entropie)
- **Stockage** : hashée SHA-256 en base SQLite — la clé brute n'est jamais persistée
- **Rate limit** : 3 clés par IP toutes les 24h
- **Cap global** : 1000 clés actives (configurable via `MAX_KEYS`)

### Via l'UI web

Sur [https://notiftk.fly.dev](https://notiftk.fly.dev), cliquer **"Générer une clé gratuite"** :
1. La clé est générée côté serveur
2. Affichée une seule fois avec bouton "Copier"
3. Automatiquement sauvegardée dans `localStorage`

---

## Clés d'environnement (admin)

Pour un déploiement propre ou un usage restreint :

```bash
# Local
export API_KEYS=sk_live_abc123,sk_live_xyz789

# Fly.io
fly secrets set API_KEYS=sk_live_abc123,sk_live_xyz789 -a notiftk
```

Plusieurs clés séparées par des virgules — chacune est valide indépendamment.

---

## Mode sans auth

Si `API_KEYS` n'est pas défini et `REQUIRE_API_KEY` n'est pas `true`, l'auth est **désactivée** :
- Tous les endpoints sont accessibles sans clé
- Le rate limiting par IP est toujours actif
- `/health` retourne `"auth_enabled": false`

Idéal pour le développement local ou un usage personnel.

---

## Rate limiting

Actif **même sans auth**, basé sur l'IP source :

| Variable | Défaut | Description |
|---|---|---|
| `RATE_LIMIT_REQUESTS` | `120` | Max requêtes par fenêtre |
| `RATE_LIMIT_WINDOW` | `60` | Taille de la fenêtre (secondes) |

Comportement : **sliding window** par IP. Dépasser la limite retourne `HTTP 429`.

---

## Endpoints admin

Pour gérer les clés self-service, configurez `ADMIN_SECRET` :

```bash
fly secrets set ADMIN_SECRET=votre-secret-fort -a notiftk
```

Puis utilisez le header `X-Admin-Secret` :

```bash
# Lister toutes les clés
curl -H "X-Admin-Secret: votre-secret" https://notiftk.fly.dev/api/admin/keys

# Révoquer une clé
curl -X DELETE \
  -H "X-Admin-Secret: votre-secret" \
  https://notiftk.fly.dev/api/admin/keys/a1b2c3d4
```

> Sans `ADMIN_SECRET` configuré, ces endpoints retournent **404** (pas 403) pour éviter la découverte.

---

## Bonnes pratiques

- Ne jamais committer une clé dans le code — utiliser les variables d'environnement
- Utiliser `fly secrets set` (jamais `.env` en production)
- Générer des clés distinctes par intégration (une clé = un bot)
- Révoquer les clés inutilisées via l'endpoint admin
- Utiliser `label` lors de la génération pour tracer l'usage

---

## Tableau récapitulatif

| Endpoint | Auth requise | Header |
|---|---|---|
| `GET /health` | Non | — |
| `GET /api/status/{username}` | Si `auth_enabled` | `X-API-Key` |
| `GET /api/stream/{username}` | Si `auth_enabled` | `?key=` |
| `GET /api/stream?users=...` | Si `auth_enabled` | `?key=` |
| `POST /api/keys` | Non | — |
| `POST /api/watch` | Si `auth_enabled` | `X-API-Key` |
| `DELETE /api/watch/{id}` | Si `auth_enabled` | `X-API-Key` |
| `GET /api/watches` | Si `auth_enabled` | `X-API-Key` |
| `GET /api/admin/keys` | Toujours | `X-Admin-Secret` |
| `DELETE /api/admin/keys/{id}` | Toujours | `X-Admin-Secret` |
