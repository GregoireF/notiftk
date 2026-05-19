# Déploiement Fly.io

> **Option recommandée** — Gratuit, HTTPS automatique, persistance SQLite, SSE longue durée supporté.

**URL de production** : [https://notiftk.fly.dev](https://notiftk.fly.dev)

---

## Pourquoi Fly.io ?

| Avantage | Détail |
|---|---|
| **Gratuit** | 3 machines shared 256 MB, toujours actives |
| **HTTPS auto** | Certificat Let's Encrypt géré automatiquement |
| **Volume persistant** | 3 GB pour SQLite (webhooks + clés API) |
| **SSE supporté** | Connexions longue durée, pas d'auto-stop (`auto_stop_machines = false`) |
| **Région Europe** | `cdg` (Paris) — faible latence pour les utilisateurs FR |
| **CI/CD intégré** | Un token suffit pour déployer depuis GitHub Actions |

---

## Premier déploiement

### 1. Installer flyctl

```bash
# macOS / Linux
curl -L https://fly.io/install.sh | sh

# Windows
iwr https://fly.io/install.ps1 -useb | iex
```

Puis se connecter :
```bash
fly auth login
```

### 2. Créer l'app

```bash
# Lancer depuis la racine du repo
fly launch --name notiftk --region cdg --no-deploy
```

### 3. Créer le volume persistant

```bash
# Volume pour SQLite (webhooks + clés API self-service)
fly volumes create notiftk_data --size 1 --region cdg -a notiftk
```

### 4. Configurer les secrets

```bash
fly secrets set \
  API_KEYS=sk_live_abc123,sk_live_xyz789 \
  ADMIN_SECRET=votre-secret-admin-fort \
  WEBHOOKS_DB=/data/webhooks.db \
  -a notiftk
```

> **Ne jamais mettre les secrets dans `fly.toml`** — ils apparaîtraient dans git.

### 5. Déployer

```bash
fly deploy -a notiftk
```

### 6. Vérifier

```bash
curl https://notiftk.fly.dev/health
# → {"status":"ok","auth_enabled":true,...}
```

---

## Configuration `fly.toml`

```toml
app            = "notiftk"
primary_region = "cdg"

[env]
  WEBHOOKS_DB = "/data/webhooks.db"

[http_service]
  internal_port = 8080
  force_https   = true

  # SSE = connexions longues — jamais auto-stopper
  auto_stop_machines  = false
  auto_start_machines = true
  min_machines_running = 1

  [http_service.concurrency]
    type       = "connections"
    hard_limit = 500
    soft_limit = 400

[[vm]]
  memory   = "256mb"
  cpu_kind = "shared"
  cpus     = 1

[[mounts]]
  source      = "notiftk_data"
  destination = "/data"

[[http_service.checks]]
  grace_period = "10s"
  interval     = "30s"
  method       = "GET"
  path         = "/health"
  timeout      = "5s"
```

---

## CI/CD GitHub Actions

Chaque push sur `main` déclenche : `lint → tests → deploy`.

### Configurer le secret GitHub

```bash
# Créer un token de deploy
fly tokens create deploy -a notiftk
# Copier la valeur affichée
```

Dans GitHub → Settings → Secrets → Actions → **New repository secret** :
- Name : `FLY_API_TOKEN`
- Value : le token copié

Le workflow `.github/workflows/ci.yml` prend en charge le reste automatiquement.

---

## Commandes utiles

```bash
# Logs en temps réel
fly logs -a notiftk

# Shell sur la machine
fly ssh console -a notiftk

# Santé + déploiements récents
fly status -a notiftk

# Lister les volumes
fly volumes list -a notiftk

# Lister les secrets (noms seulement)
fly secrets list -a notiftk

# Modifier un secret
fly secrets set ADMIN_SECRET=nouveau-secret -a notiftk

# Voir la configuration actuelle
fly config show -a notiftk

# Rollback vers la version précédente
fly releases -a notiftk          # lister
fly deploy --image <image-ref>   # déployer une version précise
```

---

## Accès à la base SQLite

```bash
# Ouvrir un shell
fly ssh console -a notiftk

# Dans le shell
sqlite3 /data/webhooks.db

# Quelques requêtes utiles
.tables
SELECT * FROM api_keys WHERE is_active = 1;
SELECT * FROM webhooks;
```

---

## Limitation trial

Sans carte bancaire, Fly.io coupe les machines après **5 minutes d'inactivité**.

- EventSource se reconnecte automatiquement — aucune donnée perdue
- Ajouter une CB sur [fly.io/dashboard](https://fly.io/dashboard) → Billing pour désactiver ce comportement
- Le free tier reste **gratuit** avec CB — elle sert uniquement de garantie

---

## Multi-instances (Stage 2)

Si vous dépassez ~100 connexions SSE simultanées, envisagez d'ajouter une instance :

```bash
# Ajouter une machine dans la même région
fly machine clone --region cdg -a notiftk

# Ou scaler le count
fly scale count 2 -a notiftk
```

> Avec plusieurs instances, le cache RAM n'est plus partagé. Ajouter Upstash Redis (free tier) pour partager le cache TikTok entre instances.
