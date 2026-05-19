# NotiTFK — Documentation

> API REST + SSE pour détecter en temps réel si un utilisateur TikTok est en live.
> **Production** : [https://notiftk.fly.dev](https://notiftk.fly.dev) · **Docs interactives** : [https://notiftk.fly.dev/docs](https://notiftk.fly.dev/docs)

---

## Navigation

### Démarrage
| Guide | Description |
|---|---|
| [Démarrage rapide](getting-started.md) | Installation, premier appel API en 2 minutes |
| [Authentification](authentication.md) | Clés API (env + self-service), rate limiting |
| [Référence API](api-reference.md) | Tous les endpoints, codes HTTP, exemples |

### Intégrations
| Guide | Description |
|---|---|
| [Bot Discord — slash command](integrations/discord-slash.md) | Commande `/live ninja` avec statut actuel |
| [Bot Discord — notifications SSE](integrations/discord-sse.md) | Notification auto quand un streamer démarre |
| [Widget web — bulle live/offline](integrations/web-widget.md) | Bulle verte pulsante sur votre site |
| [Webhooks](integrations/webhooks.md) | POST callback sans connexion persistante |
| [Clients REST](integrations/rest-clients.md) | cURL, Python, PHP, Node.js, Go |

### Déploiement
| Guide | Description |
|---|---|
| [Oracle Cloud Always Free](deployment/oracle.md) | VM ARM gratuite, toujours active, SQLite persistant |
| [Fly.io](deployment/flyio.md) | PaaS simple avec volume SQLite |
| [Local / développement](deployment/local.md) | Dev avec hot-reload, tests, pre-commit hooks |
| [Autres plateformes](deployment/alternatives.md) | Koyeb, Render, Docker — comparatif complet |

### Référence technique
| Guide | Description |
|---|---|
| [Architecture](architecture.md) | Flux de détection TikTok, cache, SSE, scalabilité |
| [Sécurité](security.md) | SSRF, HMAC, bonnes pratiques |
| [Variables d'environnement](env-reference.md) | Toutes les variables avec valeurs par défaut |

---

## En un coup d'œil

```
POST /api/keys                   Générer une clé API (self-service, gratuit)
GET  /api/status/{username}      Statut live ponctuel (REST, cache 30s)
GET  /api/stream/{username}      Statut en temps réel (SSE change-only)
GET  /api/stream?users=a,b,c     Multi-streamers en une seule connexion
POST /api/watch                  Enregistrer un webhook
DELETE /api/watch/{id}           Supprimer un webhook
GET  /api/watches                Lister tous les webhooks
GET  /api/admin/keys             [Admin] Lister toutes les clés
DELETE /api/admin/keys/{id}      [Admin] Révoquer une clé
GET  /health                     Liveness check
```

---

## Liens rapides

- **Tester en live** : [https://notiftk.fly.dev](https://notiftk.fly.dev)
- **Swagger UI** : [https://notiftk.fly.dev/docs](https://notiftk.fly.dev/docs)
- **ReDoc** : [https://notiftk.fly.dev/redoc](https://notiftk.fly.dev/redoc)
- **Health check** : [https://notiftk.fly.dev/health](https://notiftk.fly.dev/health)
- **GitHub** : dépôt source
- **TikTokLive** : [https://github.com/isaackogan/TikTokLive](https://github.com/isaackogan/TikTokLive)
