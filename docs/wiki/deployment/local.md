# Développement local

---

## Installation

```bash
git clone <repo-url> && cd notiftk
pip install -r requirements.txt
```

### Pre-commit hooks (recommandé)

```bash
make hooks   # installe ruff comme hook pre-commit
```

---

## Lancer le serveur

```bash
make dev
# → uvicorn api.main:app --reload --port 8000
```

| URL | Description |
|---|---|
| http://localhost:8000 | UI de test (formulaire username) |
| http://localhost:8000/docs | Swagger UI interactif |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/health | Health check |

---

## Variables d'environnement locales

```bash
# Auth désactivée par défaut — activer avec :
export API_KEYS=sk_live_abc123

# Ou via un fichier .env (non versionné)
cp .env.example .env   # si disponible
```

Variables utiles en dev :

```bash
export API_KEYS=sk_live_test123          # active l'auth
export ADMIN_SECRET=dev-admin            # active les endpoints admin
export WEBHOOKS_DB=data/webhooks.db      # chemin SQLite (créé automatiquement)
export MAX_KEYS_PER_IP=100               # lever la limite pour les tests
```

---

## Tests

```bash
make test          # tests unitaires (sans réseau TikTok)
make test-all      # inclut test d'intégration TikTok réel
make lint          # ruff check (style)
make fmt           # ruff format (auto-correction)
make check         # lint + test — à lancer avant chaque push
```

### Structure des tests

```
tests/
├── conftest.py         — fixtures partagées (clean_webhooks autouse)
├── test_tiktok.py      — détection live, cache, erreurs TikTok
├── test_api.py         — endpoints HTTP (REST, SSE, webhooks)
├── test_webhooks.py    — SSRF, limites, persistance, dispatch HMAC
└── test_auth.py        — rate limiting, authentification
```

### Tester un endpoint manuellement

```bash
# Sans auth
curl http://localhost:8000/api/status/ninja

# Avec auth
curl -H "X-API-Key: sk_live_test123" http://localhost:8000/api/status/ninja

# SSE (Ctrl+C pour arrêter)
curl -N "http://localhost:8000/api/stream/ninja?key=sk_live_test123"

# Générer une clé
curl -X POST http://localhost:8000/api/keys

# Admin — lister les clés
curl -H "X-Admin-Secret: dev-admin" http://localhost:8000/api/admin/keys
```

---

## Makefile — référence complète

```makefile
make dev        # uvicorn --reload sur :8000
make test       # pytest -m "not integration"
make test-all   # pytest (tous les tests, incluant integration)
make lint       # ruff check api/ tests/
make fmt        # ruff format api/ tests/
make check      # lint + test
make hooks      # pre-commit install
```

---

## Workflow de contribution

```bash
# 1. Créer une branche
git checkout -b feat/ma-feature

# 2. Développer + tester
make dev        # hot-reload
make check      # lint + tests avant commit

# 3. Commit (le hook ruff vérifie le style automatiquement)
git add api/ma-feature.py tests/test_ma_feature.py
git commit -m "feat: description courte"

# 4. Push + PR
git push origin feat/ma-feature
# → ouvrir une PR sur GitHub
```
