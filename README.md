# NotiTK

API REST + SSE pour dÃ©tecter en temps rÃ©el si un utilisateur TikTok est en live.

ConÃ§ue pour alimenter des **bots Discord**, des **widgets de site web** (bulle verte/rouge), ou tout systÃ¨me qui doit rÃ©agir quand un streamer passe en ligne â€” sans UI, sans polling cÃ´tÃ© client, sans dÃ©pendance Ã  l'API officielle TikTok (qui n'existe pas pour le statut live).

---

## Sommaire

- [DÃ©marrage rapide](#dÃ©marrage-rapide)
- [RÃ©ponse API](#rÃ©ponse-api)
- [Cas d'utilisation](#cas-dutilisation)
  - [Bot Discord â€” commande slash](#1-bot-discord--commande-slash)
  - [Bot Discord â€” notification automatique](#2-bot-discord--notification-automatique)
  - [Site web â€” bulle live/offline](#3-site-web--bulle-liveoffline)
  - [Webhooks â€” sans connexion persistante](#4-webhooks--sans-connexion-persistante)
  - [Appel REST simple](#5-appel-rest-simple)
- [API Reference](#api-reference)
- [Authentification & rate limiting](#authentification--rate-limiting)
- [SÃ©curitÃ©](#sÃ©curitÃ©)
- [DÃ©ploiement Ã  0 â‚¬](#dÃ©ploiement-Ã -0-)
- [Tests & CI](#tests--ci)
- [Contribuer](#contribuer)
- [Architecture & dÃ©cisions techniques](#architecture--dÃ©cisions-techniques)
- [RÃ©silience & mises Ã  jour TikTok](#rÃ©silience--mises-Ã -jour-tiktok)
- [Roadmap](#roadmap)

---

## DÃ©marrage rapide

```bash
git clone <repo-url> && cd notiftk
pip install -r requirements.txt
make dev          # lance le serveur sur http://localhost:8000
```

| Commande | Description |
|---|---|
| `make dev` | Serveur local avec hot-reload |
| `make test` | Tests unitaires (sans rÃ©seau) |
| `make lint` | VÃ©rification du style |
| `make fmt` | Auto-correction du style |
| `make hooks` | Installe les pre-commit hooks (Ã  faire une fois) |
| `make check` | Lint + tests â€” Ã  lancer avant chaque push |

- **UI de test** : http://localhost:8000
- **Docs API interactives** : http://localhost:8000/docs

---

## RÃ©ponse API

Toutes les rÃ©ponses partagent ce format JSON :

```json
{
  "username":     "ninja",
  "is_live":      true,
  "room_id":      "7496121315238087466",
  "viewer_count": 12500,
  "title":        "Fortnite ranked grind"
}
```

**Quand offline** â€” `room_id`, `viewer_count` et `title` sont `null`.

**Cas spÃ©cial â€” stream 18+** â€” `is_live: true`, `room_id` renseignÃ©, `viewer_count` et `title` Ã  `null` (TikTok refuse les dÃ©tails sans session authentifiÃ©e).

**Erreur** (SSE uniquement) :
```json
{ "username": "ninja", "error": "...", "transient": true }
```
- `transient: true` â†’ flap temporaire, garder la connexion
- Pas de `transient` â†’ erreur fatale (user inexistant), fermer

---

## Cas d'utilisation

### 1. Bot Discord â€” commande slash

RÃ©pond Ã  `/live ninja` avec le statut actuel. Appel REST one-shot, rÃ©sultat mis en cache 30s cÃ´tÃ© serveur.

```js
// npm install discord.js node-fetch
const { REST, Routes, Client, GatewayIntentBits } = require('discord.js');
const fetch = require('node-fetch');

const NOTIFTK = 'https://notiftk.fly.dev';
const KEY    = process.env.NOTIFTK_KEY;   // undefined = auth dÃ©sactivÃ©e

async function getLiveStatus(username) {
  const headers = KEY ? { 'X-API-Key': KEY } : {};
  const res = await fetch(`${NOTITK}/api/status/${username}`, { headers });

  if (res.status === 404) return null;           // user inexistant
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  return res.json();
  // { username, is_live, room_id, viewer_count, title }
}

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.on('interactionCreate', async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  if (interaction.commandName !== 'live') return;

  await interaction.deferReply();
  const username = interaction.options.getString('username');

  try {
    const data = await getLiveStatus(username);
    if (!data) {
      return interaction.editReply(`âŒ @${username} est introuvable sur TikTok.`);
    }
    if (data.is_live) {
      const viewers = data.viewer_count ? ` Â· ${data.viewer_count.toLocaleString()} viewers` : '';
      const title   = data.title ? `${NOTIFTK}n> ${data.title}` : '';
      interaction.editReply(`ðŸ”´ **@${username}** est EN LIVE${viewers}${title}`);
    } else {
      interaction.editReply(`âš« **@${username}** est hors ligne.`);
    }
  } catch (err) {
    interaction.editReply(`âš ï¸ Erreur lors de la vÃ©rification : ${err.message}`);
  }
});

client.login(process.env.DISCORD_TOKEN);
```

---

### 2. Bot Discord â€” notification automatique

Se connecte via SSE et envoie un message dans un channel dÃ¨s que le streamer passe en live. Fonctionne en arriÃ¨re-plan, sans polling cÃ´tÃ© bot.

```js
// npm install eventsource
const { EventSource } = require('eventsource');

const NOTIFTK     = 'https://notiftk.fly.dev';
const KEY        = process.env.NOTIFTK_KEY;
const CHANNEL_ID = '123456789012345678';

function watchStreamer(discordClient, username) {
  const url = KEY
    ? `${NOTITK}/api/stream/${username}?key=${KEY}`
    : `${NOTITK}/api/stream/${username}`;

  let wasLive = null;
  const src   = new EventSource(url);

  src.onmessage = async ({ data }) => {
    const status = JSON.parse(data);

    // Erreur fatale (user inexistant) â€” on arrÃªte
    if (status.error && !status.transient) {
      console.error(`[NotiTK] ${username}: ${status.error}`);
      src.close();
      return;
    }

    // Ignore les erreurs temporaires â€” SSE continue tout seul
    if (status.error) return;

    // Notification Ã  la transition offline â†’ live uniquement
    if (status.is_live && wasLive === false) {
      const channel = await discordClient.channels.fetch(CHANNEL_ID);
      const viewers = status.viewer_count
        ? ` Â· ${status.viewer_count.toLocaleString()} viewers`
        : '';
      channel.send(
        `ðŸ”´ **@${username}** vient de commencer un live${viewers} !${NOTIFTK}n` +
        (status.title ? `> ${status.title}` : '')
      );
    }

    wasLive = status.is_live;
  };

  // EventSource se reconnecte automatiquement sur erreur rÃ©seau
  src.onerror = (err) => console.warn(`[NotiTK] Reconnexion pour ${username}...`);

  return src; // garder une rÃ©fÃ©rence pour src.close() si besoin
}

// Surveiller plusieurs streamers
client.once('ready', () => {
  ['ninja', 'shroud', 'pokimane'].forEach((u) => watchStreamer(client, u));
});
```

---

### 3. Site web â€” bulle live/offline

Ajoute une bulle verte pulsante sur une page. Pas de backend requis cÃ´tÃ© site â€” SSE direct depuis le navigateur.

```html
<!-- Ajouter dans votre page -->
<div id="live-badge" style="display:none">
  <span id="live-dot"></span>
  <span id="live-text"></span>
</div>

<style>
  #live-dot {
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 50%;
    margin-right: 6px;
  }
  .live   { background: #22c55e; animation: pulse 1.8s infinite; }
  .offline{ background: #6b7280; }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0   rgba(34,197,94,.6); }
    70%  { box-shadow: 0 0 0 8px rgba(34,197,94,0);  }
    100% { box-shadow: 0 0 0 0   rgba(34,197,94,0);  }
  }
</style>

<script>
const USERNAME = 'ninja';
const API_KEY  = '';  // laisser vide si auth dÃ©sactivÃ©e

const url = API_KEY
  ? `/api/stream/${USERNAME}?key=${API_KEY}`
  : `/api/stream/${USERNAME}`;

const src    = new EventSource(url);
const badge  = document.getElementById('live-badge');
const dot    = document.getElementById('live-dot');
const text   = document.getElementById('live-text');

src.onmessage = ({ data }) => {
  const s = JSON.parse(data);
  if (s.error && !s.transient) { src.close(); return; }
  if (s.error) return;

  badge.style.display = 'inline-flex';
  dot.className  = s.is_live ? 'live' : 'offline';
  text.textContent = s.is_live
    ? `EN LIVE${s.viewer_count ? ` Â· ${s.viewer_count.toLocaleString()}` : ''}`
    : 'Hors ligne';
};
</script>
```

---

### 4. Webhooks â€” sans connexion persistante

Le bot reÃ§oit un POST Ã  chaque changement `is_live`, sans maintenir de connexion ouverte. IdÃ©al pour les bots serverless ou les intÃ©grations qui ne peuvent pas garder un EventSource actif.

```js
// Enregistrer un webhook (une seule fois, au dÃ©marrage du bot)
const res = await fetch(`${NOTITK}/api/watch`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-API-Key': KEY },
  body: JSON.stringify({
    username: 'ninja',
    callback_url: 'https://ton-bot.example.com/notiftk-hook',
    secret: process.env.NOTIFTK_WEBHOOK_SECRET,  // optionnel â€” HMAC-SHA256
  }),
});
const { watch_id } = await res.json();
// Stocker watch_id pour pouvoir se dÃ©senregistrer plus tard

// Recevoir les notifications (cÃ´tÃ© bot â€” Express, Fastify, etc.)
app.post('/notiftk-hook', (req, res) => {
  // VÃ©rifier la signature si secret configurÃ©
  const sig = req.headers['x-notitk-signature'];
  if (sig) {
    const expected = 'sha256=' + hmac.createHmac('sha256', process.env.NOTIFTK_WEBHOOK_SECRET)
      .update(JSON.stringify(req.body)).digest('hex');
    if (sig !== expected) return res.sendStatus(401);
  }

  const { username, is_live, viewer_count, title } = req.body;
  if (is_live) {
    console.log(`ðŸ”´ ${username} est en live ! ${viewer_count ?? ''} viewers`);
    // envoyer message Discord, dÃ©clencher une action, etc.
  } else {
    console.log(`âš« ${username} a terminÃ© son live.`);
  }
  res.sendStatus(200);
});

// Se dÃ©senregistrer
await fetch(`${NOTITK}/api/watch/${watch_id}`, {
  method: 'DELETE',
  headers: { 'X-API-Key': KEY },
});
```

**Persistance** : les webhooks survivent aux redÃ©marrages du serveur (SQLite). Sur Fly.io, monter un volume persistent et configurer `WEBHOOKS_DB=/data/webhooks.db`.

**Limites** : 5 webhooks max par username, 100 au total (configurables via `MAX_WEBHOOKS_PER_USER` / `MAX_WEBHOOKS`).

---

### 5. Appel REST simple

Fonctionne depuis n'importe quel langage â€” Python, PHP, cURL, etc.

**cURL :**
```bash
# Sans auth
curl https://notiftk.fly.dev/api/status/ninja

# Avec auth
curl -H "X-API-Key: sk_live_abc123" https://notiftk.fly.dev/api/status/ninja
```

**Python :**
```python
import httpx

def is_live(username: str, api_key: str | None = None) -> dict:
    headers = {"X-API-Key": api_key} if api_key else {}
    r = httpx.get(f"https://notiftk.fly.dev/api/status/{username}", headers=headers)
    r.raise_for_status()
    return r.json()
    # {"username": "ninja", "is_live": True, "viewer_count": 12500, ...}
```

**PHP :**
```php
$data = json_decode(file_get_contents(
    'https://notiftk.fly.dev/api/status/ninja',
    false,
    stream_context_create(['http' => ['header' => 'X-API-Key: sk_live_abc123']])
), true);

echo $data['is_live'] ? 'EN LIVE' : 'Hors ligne';
```

---

## API Reference

### `GET /health`
Liveness check â€” ne touche pas TikTok.

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

---

### `GET /api/status/{username}`
VÃ©rification ponctuelle. Cache serveur **30s**.

**Auth :** header `X-API-Key: sk_live_xxx`

| Code | Cas |
|------|-----|
| `200` | SuccÃ¨s (live ou offline) |
| `401` | ClÃ© API manquante ou invalide |
| `404` | Utilisateur inexistant ou jamais allÃ© en live |
| `422` | Username invalide (caractÃ¨res interdits ou > 24 chars) |
| `429` | Rate limit dÃ©passÃ© |
| `502` | Erreur TikTok (timeout, API indisponible) |

---

### `GET /api/stream/{username}`
Server-Sent Events â€” **change-only** : Ã©vÃ©nement envoyÃ© uniquement quand `is_live` change, + commentaire `: heartbeat` toutes les 30s.

**Auth :** query param `?key=sk_live_xxx` (EventSource ne supporte pas les headers).

```
# Premier Ã©vÃ©nement + Ã  chaque transition live/offline
data: {"username":"ninja","is_live":true,"room_id":"...","viewer_count":12500,"title":"..."}

data: {"username":"ninja","is_live":false,"room_id":null,"viewer_count":null,"title":null}

# Erreur temporaire (TikTok flap) â€” continuer Ã  Ã©couter
data: {"username":"ninja","error":"TikTok API timed out","transient":true}

# Erreur fatale (user inexistant) â€” fermer la connexion
data: {"username":"ghost","error":"User not found"}

# Keepalive (commentaire SSE, ignorÃ© par EventSource)
: heartbeat
```

---

### `GET /api/stream?users=u1,u2,u3`
SSE multi-usernames â€” une seule connexion pour surveiller jusqu'Ã  **10 streamers**. Chaque Ã©vÃ©nement inclut le champ `username`.

```
data: {"username":"ninja","is_live":true,...}
data: {"username":"pokimane","is_live":false,...}
: heartbeat
```

---

### `POST /api/watch`
Enregistre un webhook. NotiTK postera Ã  l'URL donnÃ©e Ã  chaque changement `is_live`.

**Corps :**
```json
{ "username": "ninja", "callback_url": "https://ton-bot.example.com/hook", "secret": "optionnel" }
```

**RÃ©ponse (201) :**
```json
{ "watch_id": "uuid", "username": "ninja", "callback_url": "https://..." }
```

| Code | Cas |
|------|-----|
| `201` | Webhook crÃ©Ã© |
| `422` | URL ou username invalide |
| `429` | Limite de webhooks atteinte (5/username ou 100 total) |

---

### `DELETE /api/watch/{watch_id}`
Supprime le webhook. RÃ©ponse 204 si trouvÃ©, 404 sinon.

---

### `GET /api/watches`
Liste tous les webhooks actifs. Les secrets ne sont jamais exposÃ©s.

```json
[
  { "watch_id": "uuid", "username": "ninja", "callback_url": "https://..." }
]
```

---

## Authentification & rate limiting

```bash
# GÃ©nÃ©rer une clÃ©
python -c "import secrets; print('sk_live_' + secrets.token_urlsafe(24))"

# Activer sur Fly.io
fly secrets set API_KEYS=sk_live_abc123,sk_live_xyz789

# Activer en local
export API_KEYS=sk_live_abc123
```

Sans `API_KEYS`, l'auth est dÃ©sactivÃ©e. Le **rate limiting par IP est toujours actif** (120 req/60s par dÃ©faut) mÃªme sans auth.

| Variable | DÃ©faut | Description |
|---|---|---|
| `API_KEYS` | _(vide)_ | ClÃ©s valides sÃ©parÃ©es par des virgules |
| `REQUIRE_API_KEY` | `false` | Forcer l'auth mÃªme sans clÃ©s configurÃ©es |
| `RATE_LIMIT_REQUESTS` | `120` | Max requÃªtes par fenÃªtre |
| `RATE_LIMIT_WINDOW` | `60` | Taille de la fenÃªtre (secondes) |
| `WEBHOOKS_DB` | `data/webhooks.db` | Chemin SQLite pour la persistance des webhooks |
| `MAX_WEBHOOKS` | `100` | Cap global du nombre de webhooks |
| `MAX_WEBHOOKS_PER_USER` | `5` | Cap par username |

---

## SÃ©curitÃ©

### Protection SSRF (webhooks)

Les URLs de callback sont validÃ©es Ã  l'enregistrement contre une liste de plages IP privÃ©es/loopback :

```
127.0.0.0/8     # loopback
10.0.0.0/8      # RFC-1918
172.16.0.0/12   # RFC-1918
192.168.0.0/16  # RFC-1918
169.254.0.0/16  # link-local / metadata AWS EC2
100.64.0.0/10   # shared address space
::1/128         # IPv6 loopback
fc00::/7        # IPv6 unique-local
fe80::/10       # IPv6 link-local
```

Toute tentative d'enregistrer `http://localhost/...` ou `http://192.168.1.1/...` retourne HTTP 422.

### VÃ©rifier la signature HMAC cÃ´tÃ© callback (Node.js)

```js
const crypto = require('crypto');

function verifySignature(rawBody, signature, secret) {
  const expected = 'sha256=' + crypto
    .createHmac('sha256', secret)
    .update(rawBody)          // Buffer ou string brut â€” pas l'objet parsÃ©
    .digest('hex');
  return crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected));
}

app.post('/hook', express.raw({ type: 'application/json' }), (req, res) => {
  const sig = req.headers['x-notitk-signature'];
  if (!verifySignature(req.body, sig, process.env.NOTIFTK_SECRET)) {
    return res.sendStatus(401);
  }
  const data = JSON.parse(req.body);
  // ...
  res.sendStatus(200);
});
```

> Utiliser `express.raw()` et non `express.json()` pour avoir accÃ¨s au body brut avant parsing.

### Bonnes pratiques de dÃ©ploiement

- Toujours configurer `API_KEYS` en production (`fly secrets set`, jamais dans le code)
- Utiliser HTTPS uniquement pour les URLs de callback webhook
- Faire tourner l'instance derriÃ¨re un reverse-proxy qui strip `X-Forwarded-For` si non utilisÃ©

---

## DÃ©ploiement Ã  0 â‚¬

### Option 1 â€” Fly.io (recommandÃ©)

> **Limitation trial** : sans carte bancaire enregistrÃ©e, Fly.io coupe les machines aprÃ¨s **5 minutes**. L'EventSource se reconnecte automatiquement (comportement normal du protocole SSE), mais les connexions actives sont briÃ¨vement interrompues. Pour un usage en production, ajouter une CB sur [fly.io/dashboard](https://fly.io/dashboard) â†’ Billing â€” le free tier (3 machines 256 MB) reste gratuit, la CB sert uniquement de garantie.

Fly.io offre **3 machines shared 256 MB** gratuites, toujours actives, HTTPS automatique, et **1 volume persistant** (3 GB). C'est la seule option gratuite qui supporte la persistance SQLite pour les webhooks.

```bash
# 1. Installer flyctl
#    https://fly.io/docs/hands-on/install-flyctl/

# 2. CrÃ©er l'app (une seule fois)
fly launch --name notiftk --region cdg --no-deploy

# 3. CrÃ©er le volume persistant pour les webhooks
fly volumes create notiftk_data --size 1 --region cdg

# 4. Configurer les secrets
fly secrets set API_KEYS=sk_live_abc123,sk_live_xyz789

# 5. DÃ©ployer
fly deploy

# 6. VÃ©rifier
curl https://notiftk.fly.dev/health
```

**CI/CD automatique :** chaque push sur `main` dÃ©clenche lint â†’ tests â†’ deploy.
Configurer le secret GitHub `FLY_API_TOKEN` :
```bash
fly tokens create deploy   # copier la valeur
# GitHub â†’ Settings â†’ Secrets â†’ Actions â†’ New secret â†’ FLY_API_TOKEN
```

**Commandes utiles :**
```bash
fly logs             # logs en temps rÃ©el
fly ssh console      # shell sur la machine
fly status           # santÃ© + dÃ©ploiements rÃ©cents
fly volumes list     # vÃ©rifier que le volume est montÃ©
```

### Option 2 â€” Koyeb (sans persistance webhooks)

Koyeb offre 1 service toujours actif (0,1 vCPU, 512 MB) sans carte bancaire, mais **sans stockage persistant** en tier gratuit â€” les webhooks sont perdus au redÃ©marrage.

```bash
# DÃ©ployer depuis GitHub via l'UI Koyeb
# https://app.koyeb.com â†’ New App â†’ GitHub â†’ sÃ©lectionner le repo
# Build command : pip install -r requirements.txt
# Run command   : uvicorn api.main:app --host 0.0.0.0 --port 8000
# Variables d'env : API_KEYS, REQUIRE_API_KEY
```

### Option 3 â€” Railway

Railway donne 5 $ de crÃ©dits/mois (hobby plan), suffisant pour ~500h de runtime. Pas de persistance disque gratuite.

```bash
railway login
railway init
railway up
```

### Comparatif

| | Fly.io | Koyeb | Railway |
|---|---|---|---|
| Toujours actif | âœ… | âœ… | âœ… |
| HTTPS auto | âœ… | âœ… | âœ… |
| Volume persistant | âœ… 3 GB | âŒ | âŒ |
| Webhooks persistants | âœ… | âŒ | âŒ |
| SSE / connexions longues | âœ… | âœ… | âœ… |
| Carte bancaire requise | Non | Non | Non |
| CoÃ»t | 0 â‚¬ | 0 â‚¬ | ~0 â‚¬ |

---

## Tests & CI

```bash
make test          # tests unitaires (sans rÃ©seau)
make test-all      # inclut le test d'intÃ©gration TikTok rÃ©el
make lint          # ruff check
make check         # lint + tests â€” avant chaque push
```

**72 tests unitaires** couvrent :
- DÃ©tection TikTok (live, offline, 18+, user not found, timeout, cache)
- Endpoints HTTP (REST, SSE, webhooks)
- Auth & rate limiting
- Webhooks (registration, SSRF, limits, persistence SQLite, dispatch HMAC, poll loop)

**Pipeline CI** (`.github/workflows/ci.yml`) : `lint â†’ test â†’ deploy` sur `main`.

**Dependabot** (`.github/dependabot.yml`) : PR automatique chaque lundi pour `TikTokLive` et les Actions GitHub.

**Pre-commit hooks** (ruff Ã  chaque commit) :
```bash
make hooks   # Ã  lancer une fois aprÃ¨s le clone
```

---

## Contribuer

```bash
# 1. Cloner et installer
git clone <repo-url> && cd notiftk
pip install -r requirements.txt
make hooks          # pre-commit hooks

# 2. Travailler
make dev            # serveur local sur :8000 avec hot-reload
# http://localhost:8000/docs â€” Swagger UI interactif

# 3. VÃ©rifier avant de pousser
make check          # lint + tests unitaires

# 4. Tester contre TikTok rÃ©el (optionnel)
make test-all
```

**Structure du projet :**
```
api/
  main.py      â€” endpoints FastAPI, gÃ©nÃ©rateurs SSE
  tiktok.py    â€” dÃ©tection live (TikTokLive wrapper + cache)
  webhooks.py  â€” poll loop background, dispatch HMAC, SQLite
  auth.py      â€” API keys, rate limiting sliding-window
  models.py    â€” modÃ¨les Pydantic partagÃ©s
infra/flyio/   â€” stack OpenTofu : app Fly.io + volume persistant
tests/
  conftest.py        â€” fixture async clean_webhooks (autouse)
  test_tiktok.py     â€” unitÃ© : cache, dÃ©tection, erreurs
  test_api.py        â€” HTTP : REST, SSE, webhooks endpoints
  test_webhooks.py   â€” unitÃ© : SSRF, limits, persistence, dispatch
  test_auth.py       â€” unitÃ© : rate limiting, auth
```

**Infrastructure as Code (`infra/flyio/`) :**

L'app Fly.io et le volume persistant sont gÃ©rÃ©s via [OpenTofu](https://opentofu.org) (â‰¥ 1.9).
Les machines (compute) sont crÃ©Ã©es par `fly deploy` en CI â€” sÃ©paration infra / dÃ©ploiement.

```bash
# Bootstrap (une seule fois, aprÃ¨s crÃ©ation du workspace HCP Terraform "notiftk-flyio")
cd infra/flyio
tofu init
tofu import fly_app.this notiftk
tofu import fly_volume.data vol_vz88wex7359onlxv
tofu plan   # vÃ©rifier que rien ne sera dÃ©truit
tofu apply
```

---

## Architecture & dÃ©cisions techniques

### Fonctionnement

TikTok n'a pas d'API publique pour le statut live. NotiTK passe par [TikTokLive](https://github.com/isaackogan/TikTokLive), qui utilise les mÃªmes endpoints internes que le navigateur â€” avec gÃ©nÃ©ration automatique du `msToken` anti-bot.

```
Client â†’ NotiTK â†’ TikTok (fetch_room_id) â†’ TikTok (fetch_room_info)
                 â†³ Cache REST 30s  (bots one-shot)
                 â†³ Cache SSE  5s   (partagÃ© entre N subscribers â†’ 1 appel/5s)
```

**Pourquoi pas reproduire TikTokLive ?**
Le `msToken` est gÃ©nÃ©rÃ© par du JavaScript obfusquÃ© cÃ´tÃ© client, mis Ã  jour rÃ©guliÃ¨rement par TikTok. Sans lui, toutes les requÃªtes retournent `{}`. Re-implÃ©menter Ã§a serait un travail Ã  plein temps. TikTokLive est la communautÃ© qui s'en charge â€” notre rÃ´le est de maintenir `requirements.txt` Ã  jour via Dependabot.

### Pourquoi Python ?

| | Python | Node.js | Go |
|---|---|---|---|
| TikTokLive mature | âœ… | âš ï¸ partiel | âŒ |
| SSE async natif | âœ… FastAPI | âœ… | âœ… |
| Stack Discord bots | âŒ | âœ… | âŒ |
| MaintenabilitÃ© | âœ… | âœ… | Moyenne |

Python est le bon choix tant que TikTokLive est la rÃ©fÃ©rence. Si l'Ã©chelle dÃ©passe 500+ connexions SSE, Node.js/Bun devient une migration viable. Go impliquerait de rÃ©-implÃ©menter l'auth TikTok from scratch.

### ScalabilitÃ©

```
Stage 1 â€” Actuel (0 â‚¬/mois)
  1 instance Fly.io 256 MB Â· Cache RAM Â· ~100 SSE simultanÃ©s

Stage 2 â€” Multi-instance (0â€“5 â‚¬/mois)
  N instances Fly.io + Upstash Redis (free tier)
  â†’ Cache partagÃ©, Ã©vite N appels TikTok par intervalle

Stage 3 â€” SaaS (20 â‚¬+/mois)
  Auto-scaling + Redis Pub/Sub SSE broadcaster
  â†’ 1 poll TikTok/username, N instances servent les clients
  PostgreSQL pour clÃ©s API + analytics Â· Sentry pour alertes
```

---

## RÃ©silience & mises Ã  jour TikTok

1. **TikTokLive comme couche d'abstraction** â€” quand TikTok change son API, `pip install TikTokLive --upgrade` suffit dans 90% des cas.
2. **Dependabot** â€” ouvre une PR automatique chaque lundi si une nouvelle version sort.
3. **Test d'intÃ©gration** (`pytest -m integration`) â€” valide que TikTok rÃ©pond toujours correctement avant de merger.
4. **Timeout 10s** â€” si TikTok freeze, la requÃªte Ã©choue proprement en `TikTokAPIError` plutÃ´t que de bloquer un worker.
5. **Erreurs transientes vs fatales dans SSE** â€” les flaps TikTok sont marquÃ©s `transient: true`, le gÃ©nÃ©rateur continue. Les erreurs permanentes ferment proprement la connexion.

---

## Roadmap

### Axes techniques

- [x] **Webhooks** â€” `POST /api/watch`, `DELETE /api/watch/{id}`, `GET /api/watches`. Persistance SQLite, limites par username, signature HMAC-SHA256 optionnelle. Auto-suppression aprÃ¨s 5 Ã©checs de livraison consÃ©cutifs ou user introuvable.
- [x] **SSE multi-usernames** â€” `GET /api/stream?users=user1,user2,user3` : une seule connexion pour surveiller jusqu'Ã  10 streamers.
- [x] **Ã‰vÃ©nements SSE change-only** â€” Ã©vÃ©nement uniquement quand `is_live` change + commentaire `: heartbeat` toutes les 30s. RÃ©duit la bande passante Ã— 6.
- [ ] **MÃ©triques Prometheus** â€” endpoint `/metrics` : nb requÃªtes, taux d'erreur, cache hit rate, latence TikTok.
- [ ] **Redis cache** â€” partage du cache entre instances pour le Stage 2 multi-instances.

### Axes produit

- [ ] **Dashboard multi-streamers** â€” UI pour surveiller une liste de streamers simultanÃ©ment.
- [ ] **Historique des sessions live** â€” ring buffer des N derniÃ¨res transitions avec timestamps.
- [ ] **Gestion des clÃ©s API via endpoint** â€” `POST /api/keys` pour auto-provisioning sans redÃ©marrage.


