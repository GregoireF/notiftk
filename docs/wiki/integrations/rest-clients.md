# Clients REST — cURL, Python, Node.js, PHP, Go

Exemples prêts à l'emploi pour chaque langage.

---

## cURL

```bash
# Sans auth
curl https://notiftk.fly.dev/api/status/ninja

# Avec auth
curl -H "X-API-Key: sk_live_abc123" https://notiftk.fly.dev/api/status/ninja

# SSE (stream en temps réel — Ctrl+C pour arrêter)
curl -N "https://notiftk.fly.dev/api/stream/ninja?key=sk_live_abc123"

# Générer une clé
curl -X POST https://notiftk.fly.dev/api/keys

# Enregistrer un webhook
curl -X POST https://notiftk.fly.dev/api/watch \
  -H "X-API-Key: sk_live_abc123" \
  -H "Content-Type: application/json" \
  -d '{"username":"ninja","callback_url":"https://example.com/hook"}'
```

---

## Python

### Avec httpx (async-ready)

```python
import httpx

BASE = "https://notiftk.fly.dev"
KEY  = "sk_live_abc123"

def get_status(username: str) -> dict:
    headers = {"X-API-Key": KEY} if KEY else {}
    r = httpx.get(f"{BASE}/api/status/{username}", headers=headers)
    r.raise_for_status()
    return r.json()

def is_live(username: str) -> bool:
    return get_status(username)["is_live"]

# Générer une clé
def generate_key(label: str | None = None) -> str:
    params = {"label": label} if label else {}
    r = httpx.post(f"{BASE}/api/keys", params=params)
    r.raise_for_status()
    return r.json()["key"]

# Enregistrer un webhook
def register_webhook(username: str, callback_url: str, secret: str | None = None) -> str:
    headers = {"X-API-Key": KEY, "Content-Type": "application/json"}
    body = {"username": username, "callback_url": callback_url}
    if secret:
        body["secret"] = secret
    r = httpx.post(f"{BASE}/api/watch", headers=headers, json=body)
    r.raise_for_status()
    return r.json()["watch_id"]

# Usage
if is_live("ninja"):
    print("Ninja est en live !")
```

### Avec requests (synchrone)

```python
import requests

r = requests.get(
    "https://notiftk.fly.dev/api/status/ninja",
    headers={"X-API-Key": "sk_live_abc123"}
)
data = r.json()
print(f"En live : {data['is_live']}")
if data['is_live']:
    print(f"Viewers : {data['viewer_count']}")
    print(f"Titre   : {data['title']}")
```

### SSE avec sseclient

```python
import requests
import sseclient  # pip install sseclient-py

KEY = "sk_live_abc123"
url = f"https://notiftk.fly.dev/api/stream/ninja?key={KEY}"

with requests.get(url, stream=True) as r:
    client = sseclient.SSEClient(r)
    for event in client.events():
        import json
        data = json.loads(event.data)
        if data.get("error") and not data.get("transient"):
            break
        if not data.get("error"):
            status = "EN LIVE" if data["is_live"] else "HORS LIGNE"
            print(f"{data['username']} : {status}")
```

---

## Node.js / TypeScript

```typescript
const BASE = 'https://notiftk.fly.dev';
const KEY  = process.env.NOTIFTK_KEY ?? '';

interface LiveStatus {
  username: string;
  is_live: boolean;
  room_id: string | null;
  viewer_count: number | null;
  title: string | null;
}

async function getStatus(username: string): Promise<LiveStatus> {
  const headers: Record<string, string> = KEY ? { 'X-API-Key': KEY } : {};
  const res = await fetch(`${BASE}/api/status/${username}`, { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<LiveStatus>;
}

async function generateKey(label?: string): Promise<string> {
  const url = label ? `${BASE}/api/keys?label=${encodeURIComponent(label)}` : `${BASE}/api/keys`;
  const res = await fetch(url, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json() as { key: string };
  return data.key;
}

// Usage
const status = await getStatus('ninja');
console.log(status.is_live ? '🔴 En live' : '⚫ Hors ligne');
```

---

## PHP

```php
<?php

define('NOTIFTK_BASE', 'https://notiftk.fly.dev');
define('NOTIFTK_KEY',  'sk_live_abc123');

function notiftk_status(string $username): array {
    $opts = ['http' => [
        'header' => 'X-API-Key: ' . NOTIFTK_KEY,
        'timeout' => 10,
    ]];
    $ctx  = stream_context_create($opts);
    $json = file_get_contents(NOTIFTK_BASE . '/api/status/' . urlencode($username), false, $ctx);
    if ($json === false) throw new RuntimeException('Appel API échoué');
    return json_decode($json, true);
}

function notiftk_is_live(string $username): bool {
    return notiftk_status($username)['is_live'];
}

// Usage
$data = notiftk_status('ninja');
if ($data['is_live']) {
    printf("🔴 %s est en live · %s viewers\n",
        $data['username'],
        number_format($data['viewer_count'] ?? 0)
    );
} else {
    printf("⚫ %s est hors ligne.\n", $data['username']);
}

// Avec Guzzle (recommandé pour la prod)
// composer require guzzlehttp/guzzle
/*
$client = new \GuzzleHttp\Client(['base_uri' => NOTIFTK_BASE]);
$res    = $client->get('/api/status/ninja', ['headers' => ['X-API-Key' => NOTIFTK_KEY]]);
$data   = json_decode($res->getBody(), true);
*/
```

---

## Go

```go
package main

import (
    "encoding/json"
    "fmt"
    "net/http"
)

const (
    base = "https://notiftk.fly.dev"
    key  = "sk_live_abc123"
)

type LiveStatus struct {
    Username    string  `json:"username"`
    IsLive      bool    `json:"is_live"`
    RoomID      *string `json:"room_id"`
    ViewerCount *int    `json:"viewer_count"`
    Title       *string `json:"title"`
}

func getStatus(username string) (*LiveStatus, error) {
    req, _ := http.NewRequest("GET", base+"/api/status/"+username, nil)
    req.Header.Set("X-API-Key", key)

    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    if resp.StatusCode != 200 {
        return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
    }

    var status LiveStatus
    json.NewDecoder(resp.Body).Decode(&status)
    return &status, nil
}

func main() {
    status, err := getStatus("ninja")
    if err != nil {
        fmt.Println("Erreur:", err)
        return
    }
    if status.IsLive {
        viewers := 0
        if status.ViewerCount != nil {
            viewers = *status.ViewerCount
        }
        fmt.Printf("🔴 %s est en live · %d viewers\n", status.Username, viewers)
    } else {
        fmt.Printf("⚫ %s est hors ligne.\n", status.Username)
    }
}
```

---

## Surveillance de plusieurs streamers (tous langages)

Utilisez l'endpoint `GET /api/stream?users=u1,u2,u3` pour une seule connexion SSE couvrant jusqu'à 10 streamers. Voir [web-widget.md](web-widget.md) pour les exemples JS/React/Vue.
