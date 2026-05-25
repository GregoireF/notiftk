# NotiTFK

API REST + SSE pour détecter en temps réel si un utilisateur TikTok est en live.

Conçue pour alimenter des **bots Discord**, des **widgets de site web** (bulle verte/rouge), ou tout système qui doit réagir quand un streamer passe en ligne — sans UI, sans polling côté client, sans dépendance à l'API officielle TikTok (qui n'existe pas pour le statut live).

**Documentation complète** → [`docs/wiki/`](docs/wiki/index.md)

| | |
|---|---|
| [Démarrage rapide](docs/wiki/getting-started.md) | Installation et premier appel en 2 minutes |
| [Référence API](docs/wiki/api-reference.md) | Tous les endpoints, codes HTTP, exemples |
| [Authentification](docs/wiki/authentication.md) | Clés env + self-service, rate limiting |
| [Architecture](docs/wiki/architecture.md) | Flux de détection, cache, SSE, scalabilité |
| [Déploiement Fly.io](docs/wiki/deployment/flyio.md) | Guide complet avec CI/CD |
| [Autres hébergeurs](docs/wiki/deployment/alternatives.md) | Koyeb, Oracle Cloud, Render, Docker |
| [Sécurité](docs/wiki/security.md) | SSRF, HMAC, bonnes pratiques |
| [Intégrations](docs/wiki/integrations/discord-slash.md) | Discord, webhooks, widget web, REST clients |

---

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [Réponse API](#réponse-api)
- [Cas d'utilisation](#cas-dutilisation)
  - [Bot Discord — commande slash](#1-bot-discord--commande-slash)
  - [Bot Discord — notification automatique](#2-bot-discord--notification-automatique)
  - [Site web — bulle live/offline](#3-site-web--bulle-liveoffline)
  - [Webhooks — sans connexion persistante](#4-webhooks--sans-connexion-persistante)
  - [Appel REST simple](#5-appel-rest-simple)
- [API Reference](#api-reference)
- [Authentification & clés API](#authentification--clés-api)
- [Sécurité](#sécurité)
- [Déploiement à 0 €](#déploiement-à-0-)
- [Tests & CI](#tests--ci)
- [Contribuer](#contribuer)
- [Architecture & décisions techniques](#architecture--décisions-techniques)
- [Résilience & mises à jour TikTok](#résilience--mises-à-jour-tiktok)
- [Roadmap](#roadmap)

---

## Démarrage rapide

```bash
git clone <repo-url> && cd notiftk
pip install -r requirements.txt
make dev          # lance le serveur sur http://localhost:8000
```

| Commande | Description |
|---|---|
| `make dev` | Serveur local avec hot-reload |
| `make test` | Tests unitaires (sans réseau) |
| `make lint` | Vérification du style |
| `make fmt` | Auto-correction du style |
| `make hooks` | Installe les pre-commit hooks (à faire une fois) |
| `make check` | Lint + tests — à lancer avant chaque push |

- **UI de test** : http://localhost:8000
- **Docs API interactives** : http://localhost:8000/docs

---

## Réponse API

Toutes les réponses partagent ce format JSON :

```json
{
  "username":     "ninja",
  "is_live":      true,
  "room_id":      "7496121315238087466",
  "viewer_count": 12500,
  "title":        "Fortnite ranked grind"
}
```

**Quand offline** — `room_id`, `viewer_count` et `title` sont `null`.

**Cas spécial — stream 18+** — `is_live: true`, `room_id` renseigné, `viewer_count` et `title` à `null` (TikTok refuse les détails sans session authentifiée).

**Erreur** (SSE uniquement) :
```json
{ "username": "ninja", "error": "...", "transient": true }
```
- `transient: true` → flap temporaire, garder la connexion
- Pas de `transient` → erreur fatale (user inexistant), fermer

---

## Cas d'utilisation

### 1. Bot Discord — commande slash

Répond à `/live ninja` avec le statut actuel. Appel REST one-shot, résultat mis en cache 30s côté serveur.

```js
// npm install discord.js node-fetch
const { Client, GatewayIntentBits } = require('discord.js');
const fetch = require('node-fetch');

const NOTIFTK = 'https://notiftk.fly.dev';
const KEY     = process.env.NOTIFTK_KEY;   // undefined = auth désactivée

async function getLiveStatus(username) {
  const headers = KEY ? { 'X-API-Key': KEY } : {};
  const res = await fetch(`${NOTIFTK}/api/status/${username}`, { headers });

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
      return interaction.editReply(`❌ @${username} est introuvable sur TikTok.`);
    }
    if (data.is_live) {
      const viewers = data.viewer_count ? ` · ${data.viewer_count.toLocaleString()} viewers` : '';
      const title   = data.title ? `\n> ${data.title}` : '';
      interaction.editReply(`🔴 **@${username}** est EN LIVE${viewers}${title}`);
    } else {
      interaction.editReply(`⚫ **@${username}** est hors ligne.`);
    }
  } catch (err) {
    interaction.editReply(`⚠️ Erreur lors de la vérification : ${err.message}`);
  }
});

client.login(process.env.DISCORD_TOKEN);
```

---

### 2. Bot Discord — notification automatique

Se connecte via SSE et envoie un message dans un channel dès que le streamer passe en live. Fonctionne en arrière-plan, sans polling côté bot.

```js
// npm install eventsource
const { EventSource } = require('eventsource');

const NOTIFTK     = 'https://notiftk.fly.dev';
const KEY         = process.env.NOTIFTK_KEY;
const CHANNEL_ID  = '123456789012345678';

function watchStreamer(discordClient, username) {
  const url = KEY
    ? `${NOTIFTK}/api/stream/${username}?key=${KEY}`
    : `${NOTIFTK}/api/stream/${username}`;

  let wasLive = null;
  const src   = new EventSource(url);

  src.onmessage = async ({ data }) => {
    const status = JSON.parse(data);

    // Erreur fatale (user inexistant) — on arrête
    if (status.error && !status.transient) {
      console.error(`[NotiTFK] ${username}: ${status.error}`);
      src.close();
      return;
    }

    // Ignore les erreurs temporaires — SSE continue tout seul
    if (status.error) return;

    // Notification à la transition offline → live uniquement
    if (status.is_live && wasLive === false) {
      const channel = await discordClient.channels.fetch(CHANNEL_ID);
      const viewers = status.viewer_count
        ? ` · ${status.viewer_count.toLocaleString()} viewers`
        : '';
      channel.send(
        `🔴 **@${username}** vient de commencer un live${viewers} !\n` +
        (status.title ? `> ${status.title}` : '')
      );
    }

    wasLive = status.is_live;
  };

  // EventSource se reconnecte automatiquement sur erreur réseau
  src.onerror = () => console.warn(`[NotiTFK] Reconnexion pour ${username}...`);

  return src; // garder une référence pour src.close() si besoin
}

// Surveiller plusieurs streamers
client.once('ready', () => {
  ['ninja', 'shroud', 'pokimane'].forEach((u) => watchStreamer(client, u));
});
```

---

### 3. Site web — bulle live/offline

Ajoute une bulle verte pulsante sur une page. Pas de backend requis côté site — SSE direct depuis le navigateur.

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
  .live    { background: #22c55e; animation: pulse 1.8s infinite; }
  .offline { background: #6b7280; }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0   rgba(34,197,94,.6); }
    70%  { box-shadow: 0 0 0 8px rgba(34,197,94,0);  }
    100% { box-shadow: 0 0 0 0   rgba(34,197,94,0);  }
  }
</style>

<script>
const USERNAME = 'ninja';
const API_KEY  = '';  // laisser vide si auth désactivée

const url = API_KEY
  ? `/api/stream/${USERNAME}?key=${API_KEY}`
  : `/api/stream/${USERNAME}`;

const src   = new EventSource(url);
const badge = document.getElementById('live-badge');
const dot   = document.getElementById('live-dot');
const text  = document.getElementById('live-text');

src.onmessage = ({ data }) => {
  const s = JSON.parse(data);
  if (s.error && !s.transient) { src.close(); return; }
  if (s.error) return;

  badge.style.display = 'inline-flex';
  dot.className  = s.is_live ? 'live' : 'offline';
  text.textContent = s.is_live
    ? `EN LIVE${s.viewer_count ? ` · ${s.viewer_count.toLocaleString()}` : ''}`
    : 'Hors ligne';
};
</script>
```

---

### 4. Webhooks — sans connexion persistante

Le bot reçoit un POST à chaque changement `is_live`, sans maintenir de connexion ouverte. Idéal pour les bots serverless ou les intégrations qui ne peuvent pas garder un EventSource actif.

```js
// Enregistrer un webhook (une seule fois, au démarrage du bot)
const NOTIFTK = 'https://notiftk.fly.dev';
const KEY = process.env.NOTIFTK_KEY;

const res = await fetch(`${NOTIFTK}/api/watch`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-API-Key': KEY },
  body: JSON.stringify({
    username: 'ninja',
    callback_url: 'https://ton-bot.example.com/notiftk-hook',
    secret: process.env.NOTIFTK_WEBHOOK_SECRET,  // optionnel — HMAC-SHA256
  }),
});
const { watch_id } = await res.json();
// Stocker watch_id pour pouvoir se désinscrire plus tard

// Recevoir les notifications (côté bot — Express, Fastify, etc.)
app.post('/notiftk-hook', (req, res) => {
  // Vérifier la signature si secret configuré
  const sig = req.headers['x-notiftk-signature'];
  if (sig) {
    const expected = 'sha256=' + require('crypto')
      .createHmac('sha256', process.env.NOTIFTK_WEBHOOK_SECRET)
      .update(JSON.stringify(req.body)).digest('hex');
    if (sig !== expected) return res.sendStatus(401);
  }

  const { username, is_live, viewer_count, title } = req.body;
  if (is_live) {
    console.log(`🔴 ${username} est en live ! ${viewer_count ?? ''} viewers`);
    // envoyer message Discord, déclencher une action, etc.
  } else {
    console.log(`⚫ ${username} a terminé son live.`);
  }
  res.sendStatus(200);
});

// Se désinscrire
await fetch(`${NOTIFTK}/api/watch/${watch_id}`, {
  method: 'DELETE',
  headers: { 'X-API-Key': KEY },
});
```

**Persistance** : les webhooks survivent aux redémarrages du serveur (SQLite). Sur Fly.io, monter un volume persistant et configurer `WEBHOOKS_DB=/data/webhooks.db`.

**Limites** : 5 webhooks max par username, 100 au total (configurables via `MAX_WEBHOOKS_PER_USER` / `MAX_WEBHOOKS`).

---

### 5. Appel REST simple

Fonctionne depuis n'importe quel langage — Python, PHP, cURL, etc.

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
Liveness check — ne touche pas TikTok.

```json
{
  "status": "ok",
  "version": "0.4.0",
  "auth_enabled": false,
  "cache_ttl_seconds": 30,
  "poll_interval_seconds": 5,
  "active_webhooks": 3
}
```

---

### `GET /api/status/{username}`
Vérification ponctuelle. Cache serveur **30s**.

**Auth :** header `X-API-Key: sk_live_xxx`

| Code | Cas |
|------|-----|
| `200` | Succès (live ou offline) |
| `401` | Clé API manquante ou invalide |
| `404` | Utilisateur inexistant ou jamais allé en live |
| `422` | Username invalide (caractères interdits ou > 24 chars) |
| `429` | Rate limit dépassé |
| `502` | Erreur TikTok (timeout, API indisponible) |

---

### `GET /api/stream/{username}`
Server-Sent Events — **change-only** : événement envoyé uniquement quand `is_live` change, + commentaire `: heartbeat` toutes les 30s.

**Auth :** query param `?key=sk_live_xxx` (EventSource ne supporte pas les headers).

```
# Premier événement + à chaque transition live/offline
data: {"username":"ninja","is_live":true,"room_id":"...","viewer_count":12500,"title":"..."}

data: {"username":"ninja","is_live":false,"room_id":null,"viewer_count":null,"title":null}

# Erreur temporaire (TikTok flap) — continuer à écouter
data: {"username":"ninja","error":"TikTok API timed out","transient":true}

# Erreur fatale (user inexistant) — fermer la connexion
data: {"username":"ghost","error":"User not found"}

# Keepalive (commentaire SSE, ignoré par EventSource)
: heartbeat
```

---

### `GET /api/stream?users=u1,u2,u3`
SSE multi-usernames — une seule connexion pour surveiller jusqu'à **10 streamers**. Chaque événement inclut le champ `username`.

```
data: {"username":"ninja","is_live":true,...}
data: {"username":"pokimane","is_live":false,...}
: heartbeat
```

---

### `POST /api/watch`
Enregistre un webhook. NotiTFK postera à l'URL donnée à chaque changement `is_live`.

**Corps :**
```json
{ "username": "ninja", "callback_url": "https://ton-bot.example.com/hook", "secret": "optionnel" }
```

**Réponse (201) :**
```json
{ "watch_id": "uuid", "username": "ninja", "callback_url": "https://..." }
```

| Code | Cas |
|------|-----|
| `201` | Webhook créé |
| `422` | URL ou username invalide |
| `429` | Limite de webhooks atteinte (5/username ou 100 total) |

---

### `DELETE /api/watch/{watch_id}`
Supprime le webhook. Réponse 204 si trouvé, 404 sinon.

---

### `GET /api/watches`
Liste tous les webhooks actifs. Les secrets ne sont jamais exposés.

```json
[
  { "watch_id": "uuid", "username": "ninja", "callback_url": "https://..." }
]
```

---

### `POST /api/keys`
Génère une nouvelle clé API en self-service. La clé brute est retournée **une seule fois** et ne peut pas être récupérée ensuite.

**Pas d'auth requise.** Limité à 3 clés par IP toutes les 24h.

| Paramètre | Type | Description |
|-----------|------|-------------|
| `label` | string (opt.) | Nom lisible pour identifier la clé (max 100 chars) |
| `expires_in` | int (opt.) | TTL en secondes (max 31 536 000 = 1 an). Omis = jamais expirée |
| `invite` | string (opt.) | Code d'invitation — requis si `KEY_INVITE_CODE` est configuré côté serveur |

**Réponse (201) :**
```json
{
  "id": "a1b2c3d4",
  "key": "sk_live_...",
  "expires_at": 1748000000.0,
  "warning": "Copiez cette clé maintenant — elle ne sera plus affichée."
}
```
`expires_at` est `null` pour les clés sans expiration.

| Code | Cas |
|------|-----|
| `201` | Clé générée |
| `403` | Code d'invitation absent ou invalide |
| `422` | Label > 100 chars ou `expires_in` invalide |
| `429` | Limite atteinte (3/IP/24h ou cap global) |

```bash
# Clé sans expiration
curl -X POST https://notiftk.fly.dev/api/keys

# Avec label et durée de vie 7 jours
curl -X POST "https://notiftk.fly.dev/api/keys?label=bot-discord&expires_in=604800"

# Avec code d'invitation (si KEY_INVITE_CODE configuré)
curl -X POST "https://notiftk.fly.dev/api/keys?invite=mon-code-secret&label=mon-bot"
```

---

### `GET /api/keys/verify`
Vérifie qu'une clé API est valide et non expirée. **Ne touche pas TikTok.** Endpoint léger à appeler avant d'ouvrir un EventSource.

**Auth :** header `X-API-Key` ou `?key=`

**Réponse (200) :**
```json
{ "valid": true }
```

| Code | Cas |
|------|-----|
| `200` | Clé valide |
| `401` | Clé absente, invalide ou expirée |

```bash
curl -H "X-API-Key: sk_live_..." https://notiftk.fly.dev/api/keys/verify
# ou
curl "https://notiftk.fly.dev/api/keys/verify?key=sk_live_..."
```

---

### `GET /api/admin/keys` *(admin)*
Liste toutes les clés API (actives et révoquées). Nécessite le header `X-Admin-Secret`.

```bash
curl -H "X-Admin-Secret: votre-secret" https://notiftk.fly.dev/api/admin/keys
```

**Réponse :**
```json
[
  { "id": "a1b2c3d4", "label": "mon-bot", "created_at": 1716000000.0, "is_active": true, "expires_at": null },
  { "id": "e5f6g7h8", "label": null, "created_at": 1715900000.0, "is_active": false, "expires_at": 1748000000.0 }
]
```

> Si `ADMIN_SECRET` n'est pas configuré, l'endpoint retourne **404**.

---

### `DELETE /api/admin/keys/{key_id}` *(admin)*
Révoque une clé API. Réponse 204 si trouvée, 404 sinon.

```bash
curl -X DELETE -H "X-Admin-Secret: votre-secret" https://notiftk.fly.dev/api/admin/keys/a1b2c3d4
```

---

## Authentification & clés API

NotiTFK supporte deux types de clés — elles sont équivalentes côté validation :

### Clés d'environnement (admin)

Pour un déploiement personnel ou un usage restreint, définissez les clés directement dans la variable d'environnement `API_KEYS` :

```bash
# Activer sur Fly.io
fly secrets set API_KEYS=sk_live_abc123,sk_live_xyz789

# Activer en local
export API_KEYS=sk_live_abc123
```

### Clés self-service (utilisateurs)

N'importe qui peut générer sa propre clé via `POST /api/keys` sans redémarrage du serveur. Les clés sont stockées hashées (SHA-256) dans la base SQLite.

```bash
# Générer une clé
curl -X POST https://notiftk.fly.dev/api/keys
# → { "id": "...", "key": "sk_live_...", "warning": "..." }

# L'utiliser ensuite
curl -H "X-API-Key: sk_live_..." https://notiftk.fly.dev/api/status/ninja
```

L'UI web propose également un bouton **"Générer une clé gratuite"** qui génère, affiche et sauvegarde automatiquement la clé dans `localStorage`.

### Variables d'environnement

Sans `API_KEYS`, l'auth est désactivée. Le **rate limiting par IP est toujours actif** (120 req/60s par défaut) même sans auth.

| Variable | Défaut | Description |
|---|---|---|
| `API_KEYS` | _(vide)_ | Clés admin séparées par des virgules |
| `ADMIN_SECRET` | _(vide)_ | Secret pour les endpoints `/api/admin/*` (non configuré = endpoints 404) |
| `KEY_INVITE_CODE` | _(vide)_ | Si défini, `POST /api/keys` exige `?invite=<code>` — restreint la création de clés |
| `REQUIRE_API_KEY` | `false` | Forcer l'auth même sans clés configurées |
| `RATE_LIMIT_REQUESTS` | `120` | Max requêtes par fenêtre |
| `RATE_LIMIT_WINDOW` | `60` | Taille de la fenêtre (secondes) |
| `WEBHOOKS_DB` | `data/webhooks.db` | Chemin SQLite (webhooks + clés self-service) |
| `MAX_WEBHOOKS` | `100` | Cap global du nombre de webhooks |
| `MAX_WEBHOOKS_PER_USER` | `5` | Cap par username |
| `MAX_KEYS` | `1000` | Cap global de clés self-service actives |
| `MAX_KEYS_PER_IP` | `3` | Max clés générées par IP toutes les 24h |
| `SSE_MAX_PER_KEY` | `20` | Max connexions SSE simultanées par clé (ou par IP si auth désactivée) |
| `SSE_MAX_PER_USERNAME` | `50` | Max connexions SSE simultanées sur un même username |
| `LOG_FORMAT` | `text` | Format des logs : `text` (humain) ou `json` (structuré pour Loki/journald) |
| `LOG_LEVEL` | `INFO` | Niveau de log : `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Sécurité

### Protection SSRF (webhooks)

Les URLs de callback sont validées à l'enregistrement contre une liste de plages IP privées/loopback :

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

### Vérifier la signature HMAC côté callback (Node.js)

```js
const crypto = require('crypto');

function verifySignature(rawBody, signature, secret) {
  const expected = 'sha256=' + crypto
    .createHmac('sha256', secret)
    .update(rawBody)          // Buffer ou string brut — pas l'objet parsé
    .digest('hex');
  return crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected));
}

app.post('/hook', express.raw({ type: 'application/json' }), (req, res) => {
  const sig = req.headers['x-notiftk-signature'];
  if (!verifySignature(req.body, sig, process.env.NOTIFTK_SECRET)) {
    return res.sendStatus(401);
  }
  const data = JSON.parse(req.body);
  // ...
  res.sendStatus(200);
});
```

> Utiliser `express.raw()` et non `express.json()` pour avoir accès au body brut avant parsing.

### Bonnes pratiques de déploiement

- Toujours configurer `API_KEYS` et `ADMIN_SECRET` en production (`fly secrets set`, jamais dans le code)
- Utiliser HTTPS uniquement pour les URLs de callback webhook
- Faire tourner l'instance derrière un reverse-proxy qui strip `X-Forwarded-For` si non utilisé

---

## Déploiement à 0 €

> Guide complet → [docs/wiki/deployment/oracle.md](docs/wiki/deployment/oracle.md)

### Option 1 — Oracle Cloud Always Free (recommandé)

VM ARM Ampere A1 : **4 OCPUs / 24 GB RAM**, toujours active, stockage bloc persistant, **sans carte bancaire**. C'est la meilleure option pour une API longue durée avec persistance SQLite.

```bash
# 1. Créer une VM ARM (VM.Standard.A1.Flex) sur console.oracle.com
#    → Image Ubuntu 22.04, 2 OCPUs, 12 GB RAM, IP publique, port 80/443 ouverts

# 2. Créer un sous-domaine gratuit sur duckdns.org
#    → noter ton token DuckDNS et pointer l'IP Oracle

# 3. SSH dans la VM et lancer le setup en une commande
export DOMAIN=notiftk.duckdns.org
curl -fsSL https://raw.githubusercontent.com/GregoireF/notiftk/main/infra/oracle/setup.sh | bash

# 4. Configurer les secrets
nano /opt/notiftk/.env

# 5. Démarrer
sudo systemctl start notiftk
curl https://notiftk.duckdns.org/health
```

Mettre à jour après un push :
```bash
bash /opt/notiftk/infra/oracle/deploy.sh
```

### Option 2 — Koyeb (sans persistance webhooks)

Koyeb offre 1 service toujours actif (0,1 vCPU, 512 MB) sans carte bancaire, mais **sans stockage persistant** en tier gratuit — les webhooks et clés self-service sont perdus au redémarrage.

```bash
# Déployer depuis GitHub via l'UI Koyeb
# https://app.koyeb.com → New App → GitHub → sélectionner le repo
# Build command : pip install -r requirements.txt
# Run command   : uvicorn api.main:app --host 0.0.0.0 --port 8000
# Variables d'env : API_KEYS, ADMIN_SECRET, REQUIRE_API_KEY
```

### Option 3 — Oracle Cloud Always Free

2 VMs ARM 4 cœurs / 24 GB RAM au total avec stockage persistant, sans carte bancaire. Setup plus complexe mais infrastructure dédiée.

Voir le guide complet → [docs/wiki/deployment/alternatives.md](docs/wiki/deployment/alternatives.md)

### Comparatif

| | Fly.io | Koyeb | Oracle Cloud | Render |
|---|---|---|---|---|
| Toujours actif | ✅ | ✅ | ✅ | ❌ spin-down |
| HTTPS auto | ✅ | ✅ | via Caddy | ✅ |
| Volume persistant | ✅ 3 GB | ❌ | ✅ illimité | ❌ |
| Webhooks persistants | ✅ | ❌ | ✅ | ❌ |
| Clés self-service persistantes | ✅ | ❌ | ✅ | ❌ |
| SSE / connexions longues | ✅ | ✅ | ✅ | ❌ |
| Carte bancaire requise | Non | Non | Non | Non |
| Coût | 0 € | 0 € | 0 € | 0 € |

---

## Tests & CI

```bash
make test          # tests unitaires (sans réseau)
make test-all      # inclut le test d'intégration TikTok réel
make lint          # ruff check
make check         # lint + tests — avant chaque push
```

Les tests couvrent :
- Détection TikTok (live, offline, 18+, user not found, timeout, cache)
- Endpoints HTTP (REST, SSE, webhooks)
- Auth & rate limiting
- Webhooks (registration, SSRF, limits, persistence SQLite, dispatch HMAC, poll loop)
- Clés API self-service (génération, hachage, limites par IP, révocation)

**Pipeline CI** (`.github/workflows/ci.yml`) : `lint → test → deploy` sur `main`.

**Dependabot** (`.github/dependabot.yml`) : PR automatique chaque lundi pour `TikTokLive` et les Actions GitHub. Auto-merge activé pour les mises à jour mineures et de patch.

**Pre-commit hooks** (ruff à chaque commit) :
```bash
make hooks   # à lancer une fois après le clone
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
# http://localhost:8000/docs — Swagger UI interactif

# 3. Vérifier avant de pousser
make check          # lint + tests unitaires

# 4. Tester contre TikTok réel (optionnel)
make test-all
```

**Structure du projet :**
```
api/
  main.py      — endpoints FastAPI, générateurs SSE
  tiktok.py    — détection live (TikTokLive wrapper + cache)
  webhooks.py  — poll loop background, dispatch HMAC, SQLite
  auth.py      — API keys, rate limiting sliding-window
  keys.py      — gestion clés self-service (génération, hachage SHA-256, limites par IP)
  models.py    — modèles Pydantic partagés
frontend/
  index.html   — UI de test (SSE, génération de clé self-service)
infra/flyio/   — stack OpenTofu : app Fly.io
tests/
  conftest.py        — fixture async clean_webhooks (autouse)
  test_tiktok.py     — unité : cache, détection, erreurs
  test_api.py        — HTTP : REST, SSE, webhooks endpoints
  test_webhooks.py   — unité : SSRF, limits, persistence, dispatch
  test_auth.py       — unité : rate limiting, auth
```

**Infrastructure as Code (`infra/flyio/`) :**

L'app Fly.io est gérée via [OpenTofu](https://opentofu.org) (≥ 1.9) avec un backend HCP Terraform (workspace `notifk-flyio`).
Les machines (compute) sont créées par `fly deploy` en CI — séparation infra / déploiement.
Le volume persistant (`notiftk_data`) est géré via `fly volumes` (le provider Fly a un bug connu avec l'import de volumes).

```bash
# Bootstrap (une seule fois)
cd infra/flyio
tofu init
tofu plan    # vérifier que rien ne sera détruit
tofu apply
```

---

## Architecture & décisions techniques

### Fonctionnement

TikTok n'a pas d'API publique pour le statut live. NotiTFK passe par [TikTokLive](https://github.com/isaackogan/TikTokLive), qui utilise les mêmes endpoints internes que le navigateur — avec génération automatique du `msToken` anti-bot.

```
Client → NotiTFK → TikTok (fetch_room_id) → TikTok (fetch_room_info)
                ↳ Cache REST 30s  (bots one-shot)
                ↳ Cache SSE  5s   (partagé entre N subscribers → 1 appel/5s)
                   └─ verrou par username → 1 seul appel TikTok même si N clients
                      trouvent le cache expiré simultanément (double-checked locking)
```

**Pourquoi pas reproduire TikTokLive ?**
Le `msToken` est généré par du JavaScript obfusqué côté client, mis à jour régulièrement par TikTok. Sans lui, toutes les requêtes retournent `{}`. Ré-implémenter ça serait un travail à plein temps. TikTokLive est la communauté qui s'en charge — notre rôle est de maintenir `requirements.txt` à jour via Dependabot.

### Pourquoi Python ?

| | Python | Node.js | Go |
|---|---|---|---|
| TikTokLive mature | ✅ | ⚠️ partiel | ❌ |
| SSE async natif | ✅ FastAPI | ✅ | ✅ |
| Stack Discord bots | ❌ | ✅ | ❌ |
| Maintenabilité | ✅ | ✅ | Moyenne |

Python est le bon choix tant que TikTokLive est la référence. Si l'échelle dépasse 500+ connexions SSE, Node.js/Bun devient une migration viable. Go impliquerait de ré-implémenter l'auth TikTok from scratch.

### Scalabilité

```
Stage 1 — Actuel (0 €/mois)
  1 instance Fly.io 256 MB · Cache RAM · ~100 SSE simultanés

Stage 2 — Multi-instance (0–5 €/mois)
  N instances Fly.io + Upstash Redis (free tier)
  → Cache partagé, évite N appels TikTok par intervalle

Stage 3 — SaaS (20 €+/mois)
  Auto-scaling + Redis Pub/Sub SSE broadcaster
  → 1 poll TikTok/username, N instances servent les clients
  PostgreSQL pour clés API + analytics · Sentry pour alertes
```

---

## Résilience & mises à jour TikTok

1. **TikTokLive comme couche d'abstraction** — quand TikTok change son API, `pip install TikTokLive --upgrade` suffit dans 90% des cas.
2. **Dependabot** — ouvre une PR automatique chaque lundi si une nouvelle version sort.
3. **Test d'intégration** (`pytest -m integration`) — valide que TikTok répond toujours correctement avant de merger.
4. **Timeout 10s** — si TikTok freeze, la requête échoue proprement en `TikTokAPIError` plutôt que de bloquer un worker.
5. **Erreurs transientes vs fatales dans SSE** — les flaps TikTok sont marqués `transient: true`, le générateur continue. Les erreurs permanentes ferment proprement la connexion.

---

## Roadmap

### Axes techniques

- [x] **Webhooks** — `POST /api/watch`, `DELETE /api/watch/{id}`, `GET /api/watches`. Persistance SQLite, limites par username, signature HMAC-SHA256 optionnelle. Auto-suppression après 5 échecs de livraison consécutifs ou user introuvable.
- [x] **SSE multi-usernames** — `GET /api/stream?users=user1,user2,user3` : une seule connexion pour surveiller jusqu'à 10 streamers.
- [x] **Événements SSE change-only** — événement uniquement quand `is_live` change + commentaire `: heartbeat` toutes les 30s. Réduit la bande passante × 6.
- [x] **Clés API self-service** — `POST /api/keys` pour auto-provisioning sans redémarrage. Stockage hashé SHA-256, limite par IP, endpoints admin protégés par `ADMIN_SECRET`.
- [x] **Expiration des clés** — `expires_in` optionnel à la génération. Clés expirées rejetées automatiquement sans action admin.
- [x] **Invite code** — `KEY_INVITE_CODE` env var pour restreindre la création de clés en déploiement public.
- [x] **`GET /api/keys/verify`** — endpoint dédié pour valider une clé sans toucher TikTok.
- [x] **Non-blocking SQLite** — toutes les lectures DB en auth wrappées dans `asyncio.to_thread`. L'event loop n'est plus bloqué sur les I/O disque.
- [x] **Thundering herd fix** — verrou asyncio par username : 1 seul appel TikTok quand N clients trouvent le cache expiré simultanément.
- [x] **Webhook retry avec backoff** — 3 tentatives (0 s, 1 s, 2 s) par delivery. Un receiver brièvement down ne perd plus l'événement.
- [ ] **Métriques Prometheus** — endpoint `/metrics` : nb requêtes, taux d'erreur, cache hit rate, latence TikTok, connexions SSE actives.
- [ ] **Connection limit par clé** — cap sur le nombre de connexions SSE simultanées par clé (protection contre l'épuisement de ressources).
- [ ] **Historique de livraison webhook** — `GET /api/watch/{id}/deliveries` : dernières N livraisons avec statut HTTP, timestamp, nb de tentatives.
- [ ] **Redis cache** — partage du cache entre instances pour le Stage 2 multi-instances.

### Axes produit

- [ ] **Dashboard multi-streamers** — UI pour surveiller une liste de streamers simultanément.
- [ ] **Historique des sessions live** — ring buffer des N dernières transitions avec timestamps.
