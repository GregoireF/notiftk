# Intégration Discord — Commande Slash

Répond à `/live <username>` avec le statut TikTok actuel. Appel REST one-shot, résultat mis en cache 30s côté NotiTFK.

---

## Prérequis

```bash
npm install discord.js node-fetch
```

Variables d'environnement nécessaires :
- `DISCORD_TOKEN` — token du bot Discord
- `DISCORD_CLIENT_ID` — client ID de l'application Discord
- `NOTIFTK_KEY` — clé API NotiTFK (optionnel si auth désactivée)

---

## Code complet

```js
const { REST, Routes, Client, GatewayIntentBits, SlashCommandBuilder } = require('discord.js');
const fetch = require('node-fetch');

const NOTIFTK = 'https://notiftk.fly.dev';
const KEY     = process.env.NOTIFTK_KEY;

// ── Enregistrer la commande (une seule fois ou après modification) ──
const commands = [
  new SlashCommandBuilder()
    .setName('live')
    .setDescription('Vérifier si un streamer TikTok est en live')
    .addStringOption(opt =>
      opt.setName('username')
        .setDescription('Pseudo TikTok (sans @)')
        .setRequired(true)
    )
].map(c => c.toJSON());

const rest = new REST({ version: '10' }).setToken(process.env.DISCORD_TOKEN);
rest.put(
  Routes.applicationCommands(process.env.DISCORD_CLIENT_ID),
  { body: commands }
).then(() => console.log('Commandes enregistrées.'));

// ── Récupérer le statut ──
async function getLiveStatus(username) {
  const headers = KEY ? { 'X-API-Key': KEY } : {};
  const res = await fetch(`${NOTIFTK}/api/status/${username}`, { headers });

  if (res.status === 404) return null;            // user inexistant sur TikTok
  if (res.status === 429) throw new Error('rate_limit');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  return res.json();
}

// ── Bot ──
const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.on('interactionCreate', async (interaction) => {
  if (!interaction.isChatInputCommand() || interaction.commandName !== 'live') return;

  await interaction.deferReply();
  const username = interaction.options.getString('username').replace(/^@/, '');

  try {
    const data = await getLiveStatus(username);

    if (!data) {
      return interaction.editReply(`❌ **@${username}** est introuvable sur TikTok.`);
    }

    if (data.is_live) {
      const viewers = data.viewer_count
        ? ` · **${data.viewer_count.toLocaleString()}** viewers`
        : '';
      const title = data.title ? `\n> ${data.title}` : '';
      const roomUrl = data.room_id
        ? `\nhttps://www.tiktok.com/@${username}/live`
        : '';
      interaction.editReply(
        `🔴 **@${username}** est EN LIVE${viewers}${title}${roomUrl}`
      );
    } else {
      interaction.editReply(`⚫ **@${username}** est hors ligne.`);
    }
  } catch (err) {
    if (err.message === 'rate_limit') {
      interaction.editReply('⚠️ Trop de requêtes — réessayez dans quelques secondes.');
    } else {
      interaction.editReply(`⚠️ Erreur : ${err.message}`);
    }
  }
});

client.login(process.env.DISCORD_TOKEN);
```

---

## Exemple de réponse

```
🔴 @ninja est EN LIVE · 12 500 viewers
> Fortnite ranked grind
https://www.tiktok.com/@ninja/live
```

```
⚫ @ninja est hors ligne.
```

---

## Notes

- Le cache serveur de 30s évite les spams de la commande.
- Si l'utilisateur n'a jamais été en live, TikTok retourne 404 — le bot affiche le message d'erreur correspondant.
- Pour surveiller en continu (notification auto), voir [discord-sse.md](discord-sse.md).
