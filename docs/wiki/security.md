# Sécurité

---

## Protection SSRF (webhooks)

Les URLs de callback sont validées à l'enregistrement contre une blocklist de plages IP privées. Toute URL pointant vers une IP privée ou de loopback retourne `HTTP 422`.

Plages bloquées :
```
127.0.0.0/8      loopback
10.0.0.0/8       RFC-1918 (réseau privé)
172.16.0.0/12    RFC-1918 (réseau privé)
192.168.0.0/16   RFC-1918 (réseau privé)
169.254.0.0/16   link-local (metadata AWS EC2)
100.64.0.0/10    shared address space
::1/128          IPv6 loopback
fc00::/7         IPv6 unique-local
fe80::/10        IPv6 link-local
```

**Exemples bloqués :**
```
http://localhost/hook          → 422
http://127.0.0.1/hook          → 422
http://192.168.1.1/hook        → 422
http://169.254.169.254/latest  → 422 (metadata AWS)
```

**Limitation** : les noms de domaine ne sont pas résolus à l'enregistrement. Un domaine qui pointe sur une IP privée peut contourner cette protection. Assurez-vous que votre callback host est bien public.

---

## Signature HMAC-SHA256 (webhooks)

Si vous fournissez un `secret` à l'enregistrement, chaque POST webhook inclut :

```
X-NotiTFK-Signature: sha256=<hex>
```

La signature est calculée sur le body JSON brut (avant parsing) :

```python
sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
header = f"sha256={sig}"
```

### Vérification côté callback

**Toujours utiliser une comparaison en temps constant** (`hmac.compare_digest` / `crypto.timingSafeEqual`) pour résister aux timing attacks.

**Node.js :**
```js
const crypto = require('crypto');

function verify(rawBody, signature, secret) {
  const expected = 'sha256=' + crypto
    .createHmac('sha256', secret)
    .update(rawBody)  // Buffer — avant JSON.parse
    .digest('hex');
  return crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected));
}
```

**Python :**
```python
import hashlib, hmac

def verify(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

---

## Stockage des clés API

Les clés self-service ne sont **jamais stockées en clair** :

```
Génération                 Stockage SQLite
sk_live_abc123  ─SHA256→  e3b0c44...  (hash irréversible)
```

- La clé brute est retournée une seule fois à la génération
- Même l'administrateur ne peut pas récupérer une clé existante
- En cas de compromission, révoquer via `DELETE /api/admin/keys/{id}` et générer une nouvelle clé

---

## Endpoints admin protégés

Les endpoints `/api/admin/*` retournent **404** (et non 403) si `ADMIN_SECRET` n'est pas configuré :
- Évite la découverte par scan d'endpoints
- Même comportement que si la route n'existait pas

Si configuré, le header `X-Admin-Secret` est requis. Utiliser un secret fort (≥ 32 chars aléatoires) :

```bash
# Générer un secret fort
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Rate limiting

Le rate limiting est actif **même sans auth** :

- Basé sur l'IP source (header `X-Forwarded-For` en production derrière Fly.io)
- Algorithme sliding window
- Par défaut : 120 req / 60s par IP
- Sur dépassement : HTTP 429

**Note Fly.io** : Fly.io injecte `X-Forwarded-For` avec l'IP réelle du client. NotiTFK lit le premier segment de ce header.

---

## XSS dans l'UI

Les données TikTok (titre du live, username) sont échappées avant insertion dans le DOM :

```js
function escHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;  // échappe automatiquement
  return d.innerHTML;
}
```

---

## Bonnes pratiques de déploiement

- **Ne jamais versionner** `API_KEYS` ou `ADMIN_SECRET` dans git
- Utiliser `fly secrets set` pour les secrets en production
- HTTPS uniquement pour les URLs de callback webhook
- Utiliser une clé distincte par intégration (un bot = une clé)
- Révoquer régulièrement les clés inutilisées
- Configurer `MAX_KEYS` et `MAX_KEYS_PER_IP` selon votre usage prévu

---

## Signaler une vulnérabilité

Ouvrir une issue GitHub avec le label `security` ou contacter directement le mainteneur.
