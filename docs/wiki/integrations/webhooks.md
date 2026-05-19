# Intégration Webhooks

NotiTFK POSTe à votre URL à chaque changement `is_live`. Idéal pour les bots serverless, les intégrations sans connexion persistante, ou tout système qui préfère recevoir des événements plutôt que les consommer.

**Avantage vs SSE** : aucune connexion à maintenir, fonctionne avec des fonctions serverless (Vercel, Lambda, etc.)  
**Inconvénient** : légèrement plus de latence (NotiTFK doit détecter puis appeler votre URL).

---

## Flux

```
NotiTFK                         Votre serveur
   │                                 │
   │  Polling TikTok (5s)            │
   │◄────────────────────────────    │
   │                                 │
   │  is_live change                 │
   │                                 │
   │  POST /votre-hook               │
   │─────────────────────────────────►
   │                                 │
   │◄─── 200 OK ─────────────────────│
```

---

## Enregistrer un webhook

```bash
curl -X POST https://notiftk.fly.dev/api/watch \
  -H "X-API-Key: sk_live_..." \
  -H "Content-Type: application/json" \
  -d '{
    "username": "ninja",
    "callback_url": "https://ton-bot.example.com/notiftk-hook",
    "secret": "mon-secret-hmac"
  }'
```

```json
{
  "watch_id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "ninja",
  "callback_url": "https://ton-bot.example.com/notiftk-hook"
}
```

Stocker `watch_id` pour pouvoir vous désinscrire.

---

## Payload reçu

```json
{
  "username": "ninja",
  "is_live": true,
  "room_id": "7496121315238087466",
  "viewer_count": 12500,
  "title": "Fortnite ranked grind"
}
```

| Champ | Type | Description |
|---|---|---|
| `username` | string | Pseudo TikTok |
| `is_live` | bool | `true` = en live, `false` = hors ligne |
| `room_id` | string\|null | ID de la room (null si offline) |
| `viewer_count` | int\|null | Spectateurs actuels (null si offline/18+) |
| `title` | string\|null | Titre du live (null si offline/18+) |

---

## Vérifier la signature HMAC

Si vous avez fourni un `secret`, NotiTFK signe chaque POST avec `X-NotiTFK-Signature: sha256=<hex>`.

### Node.js (Express)

```js
const crypto  = require('crypto');
const express = require('express');
const app     = express();

const WEBHOOK_SECRET = process.env.NOTIFTK_WEBHOOK_SECRET;

function verifySignature(rawBody, signature) {
  const expected = 'sha256=' + crypto
    .createHmac('sha256', WEBHOOK_SECRET)
    .update(rawBody)          // rawBody = Buffer, AVANT parsing JSON
    .digest('hex');
  // Comparaison en temps constant — résistant aux timing attacks
  return crypto.timingSafeEqual(
    Buffer.from(signature),
    Buffer.from(expected)
  );
}

// IMPORTANT : express.raw() pour accéder au body brut avant parsing
app.post('/notiftk-hook', express.raw({ type: 'application/json' }), (req, res) => {
  const sig = req.headers['x-notiftk-signature'];

  if (WEBHOOK_SECRET && sig) {
    if (!verifySignature(req.body, sig)) {
      return res.sendStatus(401);
    }
  }

  const data = JSON.parse(req.body);
  const { username, is_live, viewer_count, title } = data;

  if (is_live) {
    const viewers = viewer_count ? ` · ${viewer_count.toLocaleString()} viewers` : '';
    console.log(`🔴 ${username} est en live${viewers}!`);
    // → envoyer notification Discord, déclencher une action, etc.
  } else {
    console.log(`⚫ ${username} a terminé son live.`);
  }

  res.sendStatus(200);
});

app.listen(3000);
```

### Python (FastAPI)

```python
import hashlib
import hmac
import os
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
WEBHOOK_SECRET = os.getenv("NOTIFTK_WEBHOOK_SECRET", "")

@app.post("/notiftk-hook")
async def receive_webhook(
    request: Request,
    x_notiftk_signature: str | None = Header(default=None),
):
    body = await request.body()

    if WEBHOOK_SECRET and x_notiftk_signature:
        expected = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(x_notiftk_signature, expected):
            raise HTTPException(status_code=401, detail="Signature invalide")

    data = await request.json()
    username   = data["username"]
    is_live    = data["is_live"]
    viewers    = data.get("viewer_count")
    title      = data.get("title")

    if is_live:
        v = f" · {viewers:,} viewers" if viewers else ""
        print(f"🔴 {username} est en live{v}!")
    else:
        print(f"⚫ {username} a terminé son live.")

    return {"ok": True}
```

### PHP

```php
<?php
$secret    = getenv('NOTIFTK_WEBHOOK_SECRET');
$rawBody   = file_get_contents('php://input');
$signature = $_SERVER['HTTP_X_NOTIFTK_SIGNATURE'] ?? '';

if ($secret && $signature) {
    $expected = 'sha256=' . hash_hmac('sha256', $rawBody, $secret);
    if (!hash_equals($expected, $signature)) {
        http_response_code(401);
        exit('Unauthorized');
    }
}

$data     = json_decode($rawBody, true);
$username = $data['username'];
$isLive   = $data['is_live'];
$viewers  = $data['viewer_count'];

if ($isLive) {
    $v = $viewers ? " · " . number_format($viewers) . " viewers" : "";
    error_log("🔴 {$username} est en live{$v}!");
} else {
    error_log("⚫ {$username} a terminé son live.");
}

http_response_code(200);
echo 'ok';
```

---

## Gestion du cycle de vie

```js
// Lister vos webhooks
const res = await fetch('https://notiftk.fly.dev/api/watches', {
  headers: { 'X-API-Key': KEY }
});
const watches = await res.json();
// [{ watch_id, username, callback_url }]

// Désinscrire un webhook
await fetch(`https://notiftk.fly.dev/api/watch/${watchId}`, {
  method: 'DELETE',
  headers: { 'X-API-Key': KEY }
});
```

---

## Limites & auto-suppression

| Limite | Valeur par défaut | Variable |
|---|---|---|
| Max webhooks par username | 5 | `MAX_WEBHOOKS_PER_USER` |
| Max webhooks global | 100 | `MAX_WEBHOOKS` |
| Timeout par appel | 10s | — |
| Échecs avant suppression | 5 consécutifs | — |

Un webhook est automatiquement révoqué si :
- 5 appels consécutifs échouent (erreur réseau, timeout, non-2xx)
- L'utilisateur TikTok n'existe plus (`User not found`)

---

## Déploiement serverless

### Vercel (Node.js)

```js
// api/notiftk-hook.js
export default function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).end();

  const { username, is_live } = req.body;
  if (is_live) {
    // Appel à votre logique (Discord API, DB, etc.)
  }
  res.status(200).json({ ok: true });
}
```

> Vercel parse automatiquement le body — la vérification HMAC nécessite d'utiliser `bodyParser: false` dans la config pour accéder au raw body.
