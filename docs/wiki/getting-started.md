# Démarrage rapide

> **Temps estimé** : 2 minutes pour votre premier appel, 10 minutes pour une intégration Discord complète.

---

## 1. Tester sans installation

NotiTFK est déployé en production sur [https://notiftk.fly.dev](https://notiftk.fly.dev). Vous pouvez appeler l'API directement :

```bash
# Vérifier si "ninja" est en live (sans auth)
curl https://notiftk.fly.dev/api/status/ninja
```

Réponse :
```json
{
  "username": "ninja",
  "is_live": false,
  "room_id": null,
  "viewer_count": null,
  "title": null
}
```

---

## 2. Obtenir une clé API

Si l'instance que vous utilisez a l'authentification activée, générez une clé gratuitement :

```bash
curl -X POST https://notiftk.fly.dev/api/keys
```

```json
{
  "id": "a1b2c3d4",
  "key": "sk_live_...",
  "warning": "Copiez cette clé maintenant — elle ne sera plus affichée."
}
```

> **Important** : la clé brute n'est retournée qu'une fois. Sauvegardez-la immédiatement.

Avec un label (optionnel) :
```bash
curl -X POST "https://notiftk.fly.dev/api/keys?label=mon-bot-discord"
```

---

## 3. Premier appel authentifié

```bash
# Header X-API-Key pour REST
curl -H "X-API-Key: sk_live_..." https://notiftk.fly.dev/api/status/ninja

# Query param ?key= pour SSE (EventSource ne supporte pas les headers)
curl "https://notiftk.fly.dev/api/stream/ninja?key=sk_live_..."
```

---

## 4. Installation locale

```bash
git clone <repo-url> && cd notiftk
pip install -r requirements.txt
make dev          # serveur sur http://localhost:8000
```

| Commande | Description |
|---|---|
| `make dev` | Hot-reload sur :8000 |
| `make test` | Tests unitaires |
| `make lint` | Style (ruff) |
| `make fmt` | Auto-format |
| `make hooks` | Pre-commit hooks (une fois) |
| `make check` | lint + tests |

- **UI** : [http://localhost:8000](http://localhost:8000)
- **Swagger** : [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc** : [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 5. Choisir votre intégration

| Cas d'usage | Guide recommandé | Endpoint |
|---|---|---|
| Commande slash Discord | [discord-slash](integrations/discord-slash.md) | `GET /api/status/{username}` |
| Notification auto Discord | [discord-sse](integrations/discord-sse.md) | `GET /api/stream/{username}` |
| Bulle live sur votre site | [web-widget](integrations/web-widget.md) | `GET /api/stream/{username}` |
| Webhook sans connexion persistante | [webhooks](integrations/webhooks.md) | `POST /api/watch` |
| Script one-shot | [rest-clients](integrations/rest-clients.md) | `GET /api/status/{username}` |
| Surveiller plusieurs streamers | [rest-clients](integrations/rest-clients.md) | `GET /api/stream?users=a,b` |

---

## Étapes suivantes

- [Référence API complète](api-reference.md)
- [Authentification & rate limiting](authentication.md)
- [Déployer votre propre instance](deployment/flyio.md)
