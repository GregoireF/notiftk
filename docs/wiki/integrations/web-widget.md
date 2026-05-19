# Intégration Web — Bulle live/offline

Affichez un indicateur temps réel directement sur votre site. EventSource s'exécute dans le navigateur — aucun backend requis côté site.

---

## Widget minimal (copier-coller)

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    .live-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: sans-serif;
      font-size: 14px;
    }
    .live-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }
    .live-dot.live {
      background: #22c55e;
      animation: pulse 1.8s infinite;
    }
    .live-dot.offline { background: #6b7280; }
    @keyframes pulse {
      0%   { box-shadow: 0 0 0 0   rgba(34,197,94,.6); }
      70%  { box-shadow: 0 0 0 8px rgba(34,197,94,0);  }
      100% { box-shadow: 0 0 0 0   rgba(34,197,94,0);  }
    }
  </style>
</head>
<body>

<div class="live-badge" id="badge" style="display:none">
  <div class="live-dot" id="dot"></div>
  <span id="label"></span>
</div>

<script>
const USERNAME = 'ninja';   // ← changer ici
const API_KEY  = '';        // ← clé si auth activée, sinon laisser vide

const url = API_KEY
  ? `https://notiftk.fly.dev/api/stream/${USERNAME}?key=${API_KEY}`
  : `https://notiftk.fly.dev/api/stream/${USERNAME}`;

const badge = document.getElementById('badge');
const dot   = document.getElementById('dot');
const label = document.getElementById('label');

const src = new EventSource(url);

src.onmessage = ({ data }) => {
  const s = JSON.parse(data);
  if (s.error && !s.transient) { src.close(); return; }
  if (s.error) return;

  badge.style.display = 'inline-flex';
  dot.className = 'live-dot ' + (s.is_live ? 'live' : 'offline');
  label.textContent = s.is_live
    ? `EN LIVE${s.viewer_count ? ' · ' + s.viewer_count.toLocaleString() : ''}`
    : 'Hors ligne';
};
</script>

</body>
</html>
```

---

## Widget avancé — avec titre et lien

```html
<div id="stream-card" style="display:none; border:1px solid #eee; border-radius:8px; padding:12px; max-width:300px;">
  <div id="stream-status" style="display:flex; align-items:center; gap:8px; font-weight:600; font-size:16px;">
    <div id="stream-dot" style="width:12px; height:12px; border-radius:50%;"></div>
    <span id="stream-label"></span>
  </div>
  <div id="stream-details" style="margin-top:8px; font-size:13px; color:#666;"></div>
</div>

<script>
const USERNAME = 'ninja';
const API_KEY  = '';

const url = API_KEY
  ? `https://notiftk.fly.dev/api/stream/${USERNAME}?key=${API_KEY}`
  : `https://notiftk.fly.dev/api/stream/${USERNAME}`;

const card    = document.getElementById('stream-card');
const dot     = document.getElementById('stream-dot');
const label   = document.getElementById('stream-label');
const details = document.getElementById('stream-details');

new EventSource(url).onmessage = ({ data }) => {
  const s = JSON.parse(data);
  if (s.error) return;

  card.style.display = 'block';

  if (s.is_live) {
    dot.style.background = '#22c55e';
    dot.style.animation  = 'pulse 1.8s infinite';
    label.textContent    = `@${s.username} est EN LIVE`;

    const parts = [];
    if (s.viewer_count) parts.push(`👁 ${s.viewer_count.toLocaleString()} viewers`);
    if (s.title)        parts.push(`🎙 ${s.title}`);
    parts.push(`<a href="https://www.tiktok.com/@${s.username}/live" target="_blank">Regarder →</a>`);
    details.innerHTML = parts.join('<br>');
  } else {
    dot.style.background = '#6b7280';
    dot.style.animation  = 'none';
    label.textContent    = `@${s.username} — Hors ligne`;
    details.innerHTML    = '';
  }
};
</script>
```

---

## Surveillance de plusieurs streamers

```html
<div id="streamers-list"></div>

<script>
const STREAMERS = ['ninja', 'shroud', 'pokimane'];
const API_KEY   = '';

const container = document.getElementById('streamers-list');

// Option 1 : multi-SSE (une connexion par streamer)
STREAMERS.forEach(username => {
  const el = document.createElement('div');
  el.id = `streamer-${username}`;
  el.textContent = `@${username} : ...`;
  container.appendChild(el);

  const url = API_KEY
    ? `https://notiftk.fly.dev/api/stream/${username}?key=${API_KEY}`
    : `https://notiftk.fly.dev/api/stream/${username}`;

  new EventSource(url).onmessage = ({ data }) => {
    const s = JSON.parse(data);
    if (s.error) return;
    el.innerHTML = s.is_live
      ? `🔴 <strong>@${username}</strong> EN LIVE · ${s.viewer_count?.toLocaleString() ?? ''}`
      : `⚫ @${username} — Hors ligne`;
  };
});

// Option 2 : une seule connexion SSE (plus efficace, max 10 streamers)
const multiUrl = API_KEY
  ? `https://notiftk.fly.dev/api/stream?users=${STREAMERS.join(',')}&key=${API_KEY}`
  : `https://notiftk.fly.dev/api/stream?users=${STREAMERS.join(',')}`;

new EventSource(multiUrl).onmessage = ({ data }) => {
  const s = JSON.parse(data);
  if (s.error) return;
  const el = document.getElementById(`streamer-${s.username}`);
  if (!el) return;
  el.innerHTML = s.is_live
    ? `🔴 <strong>@${s.username}</strong> EN LIVE · ${s.viewer_count?.toLocaleString() ?? ''}`
    : `⚫ @${s.username} — Hors ligne`;
};
</script>
```

---

## Intégration avec des frameworks

### React

```jsx
import { useState, useEffect } from 'react';

function LiveBadge({ username, apiKey }) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const url = apiKey
      ? `https://notiftk.fly.dev/api/stream/${username}?key=${apiKey}`
      : `https://notiftk.fly.dev/api/stream/${username}`;

    const src = new EventSource(url);
    src.onmessage = ({ data }) => {
      const s = JSON.parse(data);
      if (!s.error) setStatus(s);
    };
    return () => src.close();
  }, [username, apiKey]);

  if (!status) return null;

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{
        width: 10, height: 10, borderRadius: '50%',
        background: status.is_live ? '#22c55e' : '#6b7280',
      }} />
      {status.is_live ? `EN LIVE · ${status.viewer_count?.toLocaleString() ?? ''}` : 'Hors ligne'}
    </span>
  );
}

// Usage
<LiveBadge username="ninja" apiKey={process.env.REACT_APP_NOTIFTK_KEY} />
```

### Vue 3

```vue
<template>
  <span v-if="status" class="live-badge">
    <span :class="['dot', { live: status.is_live }]" />
    {{ status.is_live ? `EN LIVE · ${viewers}` : 'Hors ligne' }}
  </span>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';

const props = defineProps({ username: String, apiKey: String });
const status = ref(null);
let src;

const viewers = computed(() =>
  status.value?.viewer_count?.toLocaleString() ?? ''
);

onMounted(() => {
  const url = props.apiKey
    ? `https://notiftk.fly.dev/api/stream/${props.username}?key=${props.apiKey}`
    : `https://notiftk.fly.dev/api/stream/${props.username}`;
  src = new EventSource(url);
  src.onmessage = ({ data }) => {
    const s = JSON.parse(data);
    if (!s.error) status.value = s;
  };
});

onUnmounted(() => src?.close());
</script>
```

---

## Notes CORS

NotiTFK autorise `*` en CORS — vous pouvez appeler l'API depuis n'importe quel domaine.

## Notes CSP

Si votre site utilise une Content Security Policy, ajoutez :
```
connect-src https://notiftk.fly.dev;
```
