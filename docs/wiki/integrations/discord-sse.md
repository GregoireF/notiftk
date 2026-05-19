# Intégration Discord — Notifications SSE automatiques

Se connecte via Server-Sent Events et envoie un message dans un channel Discord dès qu'un streamer passe en live. Fonctionne en arrière-plan sans polling côté bot.

**Avantage vs webhooks** : connexion persistante, latence ~5s. Idéal pour un bot qui tourne en continu.  
**Vs polling manuel** : 0 appel TikTok côté bot — NotiTFK gère tout.

---

## Prérequis

```bash
npm install discord.js eventsource
```

---

## Code complet

```js
const { Client, GatewayIntentBits } = require('discord.js');
const { EventSource } = require('eventsource');

const NOTIFTK    = 'https://notiftk.fly.dev';
const KEY        = process.env.NOTIFTK_KEY;
const CHANNEL_ID = process.env.DISCORD_CHANNEL_ID;  // ID du channel de notification

// ── Surveiller un streamer ──
function watchStreamer(discordClient, username) {
  const url = KEY
    ? `${NOTIFTK}/api/stream/${encodeURIComponent(username)}?key=${KEY}`
    : `${NOTIFTK}/api/stream/${encodeURIComponent(username)}`;

  let wasLive     = null;   // null = état initial inconnu
  let retryCount  = 0;
  let source      = null;

  function connect() {
    source = new EventSource(url);

    source.onmessage = async ({ data }) => {
      retryCount = 0;  // connexion OK, réinitialiser le compteur
      let status;
      try {
        status = JSON.parse(data);
      } catch {
        return;
      }

      // Erreur fatale (user inexistant) — arrêter la surveillance
      if (status.error && !status.transient) {
        console.error(`[NotiTFK] ${username}: ${status.error} — surveillance arrêtée`);
        source.close();
        return;
      }

      // Erreur transitoire — SSE se reconnecte automatiquement, on attend
      if (status.error) {
        console.warn(`[NotiTFK] ${username}: erreur temporaire — ${status.error}`);
        return;
      }

      // Notification uniquement à la transition offline → live
      if (status.is_live && wasLive === false) {
        try {
          const channel = await discordClient.channels.fetch(CHANNEL_ID);
          const viewers = status.viewer_count
            ? ` · **${status.viewer_count.toLocaleString()}** viewers`
            : '';
          const title = status.title ? `\n> ${status.title}` : '';
          const liveUrl = `\nhttps://www.tiktok.com/@${username}/live`;

          await channel.send(
            `🔴 **@${username}** vient de commencer un live${viewers}!${title}${liveUrl}`
          );
        } catch (err) {
          console.error(`[NotiTFK] Impossible d'envoyer la notification Discord: ${err.message}`);
        }
      }

      // Notification quand le live se termine (optionnel — commenter si non désiré)
      if (!status.is_live && wasLive === true) {
        try {
          const channel = await discordClient.channels.fetch(CHANNEL_ID);
          await channel.send(`⚫ **@${username}** a terminé son live.`);
        } catch { /* ignoré */ }
      }

      wasLive = status.is_live;
    };

    // EventSource se reconnecte automatiquement sur erreur réseau
    source.onerror = () => {
      retryCount++;
      console.warn(`[NotiTFK] Reconnexion #${retryCount} pour ${username}...`);
    };
  }

  connect();
  return () => source?.close();  // renvoie une fonction stop()
}

// ── Surveiller plusieurs streamers ──
function watchMultiple(discordClient, usernames) {
  const stops = usernames.map(u => watchStreamer(discordClient, u));
  return () => stops.forEach(stop => stop());  // arrêter tous
}

// ── Bot Discord ──
const client = new Client({ intents: [GatewayIntentBits.Guilds] });

let stopAll = null;

client.once('ready', () => {
  console.log(`[NotiTFK] Bot connecté : ${client.user.tag}`);
  stopAll = watchMultiple(client, ['ninja', 'shroud', 'pokimane']);
});

// Arrêt propre
process.on('SIGINT', () => {
  stopAll?.();
  process.exit(0);
});

client.login(process.env.DISCORD_TOKEN);
```

---

## Surveiller depuis un seul SSE (multi-usernames)

Pour économiser des connexions, utilisez l'endpoint multi-usernames :

```js
function watchAll(discordClient, usernames) {
  const url = KEY
    ? `${NOTIFTK}/api/stream?users=${usernames.join(',')}&key=${KEY}`
    : `${NOTIFTK}/api/stream?users=${usernames.join(',')}`;

  const wasLive = {};

  const source = new EventSource(url);
  source.onmessage = async ({ data }) => {
    const status = JSON.parse(data);
    const { username } = status;

    if (status.error && !status.transient) return;
    if (status.error) return;

    if (status.is_live && wasLive[username] === false) {
      const channel = await discordClient.channels.fetch(CHANNEL_ID);
      await channel.send(`🔴 **@${username}** est en live !`);
    }
    wasLive[username] = status.is_live;
  };

  return () => source.close();
}

// Max 10 usernames par connexion
client.once('ready', () => {
  watchAll(client, ['ninja', 'shroud', 'pokimane', 'xqc', 'timthetatman']);
});
```

---

## Résolution des problèmes

| Symptôme | Cause | Solution |
|---|---|---|
| Reconnexions en boucle | Auth requise mais pas de clé | Vérifier `NOTIFTK_KEY`, appeler `/health` |
| Notification non envoyée | Bot sans permission dans le channel | Vérifier les permissions Discord |
| `User not found` dans les logs | Pseudo TikTok incorrect | Vérifier l'orthographe exacte du pseudo |
| `transient` errors fréquentes | TikTok API instable | Normal, SSE gère la reconnexion automatiquement |
