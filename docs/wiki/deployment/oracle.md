# Déploiement sur Oracle Cloud Always Free

> **Gratuit, toujours actif, persistance SQLite** — l'alternative idéale à Fly.io sans CB requise pour les ressources Always Free.
>
> Ce guide configure une VM ARM Ampere A1 (4 OCPUs / 24 GB RAM) avec HTTPS automatique via Caddy + DuckDNS.

---

## Prérequis

- Compte Oracle Cloud (inscription sur [cloud.oracle.com](https://cloud.oracle.com))
- Compte DuckDNS gratuit ([duckdns.org](https://www.duckdns.org)) pour le sous-domaine HTTPS
- Accès SSH depuis ta machine (clé SSH générée)

---

## Étape 1 — Créer la VM Oracle Cloud

### 1.1 — Aller dans la console Oracle

[console.oracle.com](https://console.oracle.com) → **Compute** → **Instances** → **Create Instance**

### 1.2 — Configurer l'instance

| Champ | Valeur |
|---|---|
| **Name** | `notiftk` |
| **Image** | Ubuntu 22.04 (ou 24.04) |
| **Shape** | VM.Standard.A1.Flex *(Always Free)* |
| **OCPUs** | 2 (ou 4 max — tout est gratuit) |
| **Memory** | 12 GB (ou 24 GB max) |

> **Important** : bien sélectionner **VM.Standard.A1.Flex** (ARM Ampere). C'est la shape Always Free. Ne pas choisir les shapes E2/E3 (payantes au-delà du micro).

### 1.3 — Réseau

- Laisser les valeurs par défaut (nouveau VCN + subnet)
- Cocher **Assign public IPv4 address** ✅

### 1.4 — Clé SSH

- Cliquer **Generate SSH key pair** et télécharger les deux fichiers
- Ou coller ta clé publique existante (`~/.ssh/id_rsa.pub`)

### 1.5 — Boot volume

- Taille : **50 GB** (inclus dans le quota 200 GB gratuit)

Cliquer **Create** et attendre ~2 minutes.

---

## Étape 2 — Ouvrir les ports HTTP/HTTPS

### 2.1 — Security List (pare-feu Oracle)

Dans la console : **Networking** → **Virtual Cloud Networks** → ton VCN → **Security Lists** → **Default Security List**

Ajouter deux **Ingress Rules** :

| Source | Protocol | Port | Description |
|---|---|---|---|
| `0.0.0.0/0` | TCP | `80` | HTTP (ACME challenge + redirect) |
| `0.0.0.0/0` | TCP | `443` | HTTPS |

> SSH (port 22) est déjà ouvert par défaut.

---

## Étape 3 — DuckDNS (sous-domaine gratuit)

1. Aller sur [duckdns.org](https://www.duckdns.org) → se connecter
2. Créer un sous-domaine : `notiftk` → bouton **add domain**
3. Copier l'**IP publique** de ta VM Oracle (visible dans la console) dans le champ **current ip**
4. Cliquer **update ip**
5. Vérifier : `ping notiftk.duckdns.org` → doit résoudre vers ton IP Oracle

> Garde l'onglet DuckDNS ouvert — tu auras besoin du **token** à l'étape suivante.

---

## Étape 4 — Se connecter à la VM et lancer le setup

### 4.1 — SSH dans la VM

```bash
# Remplace <oracle-ip> par l'IP publique de ta VM
ssh -i ~/Downloads/ssh-key.key ubuntu@<oracle-ip>
```

> Sur Oracle Cloud, l'utilisateur par défaut est `ubuntu` (Ubuntu) ou `opc` (Oracle Linux).

### 4.2 — Lancer le script de setup automatique

```bash
export DOMAIN=notiftk.duckdns.org

curl -fsSL https://raw.githubusercontent.com/GregoireF/notiftk/main/infra/oracle/setup.sh | bash
```

Le script fait en une passe :
- Installe Python, Git, Caddy
- Ouvre les ports 80/443 dans iptables (spécifique à Oracle Cloud)
- Clone le repo dans `/opt/notiftk`
- Crée le venv Python et installe les dépendances
- Crée `/opt/notiftk/.env` depuis le template
- Installe et active le service systemd
- Configure Caddy pour HTTPS automatique

---

## Étape 5 — Configurer les secrets

```bash
nano /opt/notiftk/.env
```

Valeurs minimales à renseigner :

```bash
WEBHOOKS_DB=/data/notiftk/webhooks.db
API_KEYS=sk_live_ton_cle_ici
ADMIN_SECRET=ton_secret_admin_ici
LOG_LEVEL=INFO
CORS_ORIGINS=*
```

Générer des valeurs sûres :
```bash
# Clé API
python3 -c "import secrets; print('sk_live_' + secrets.token_hex(24))"

# Secret admin
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Étape 6 — Démarrer et vérifier

```bash
# Démarrer l'app
sudo systemctl start notiftk

# Vérifier le statut
sudo systemctl status notiftk

# Logs en temps réel
sudo journalctl -u notiftk -f
```

Tester que tout fonctionne :

```bash
# Local (HTTP direct)
curl http://localhost:8080/health

# Public (HTTPS — peut prendre 30s pour le premier cert Let's Encrypt)
curl https://notiftk.duckdns.org/health
```

Réponse attendue :
```json
{
  "status": "ok",
  "version": "0.3.0",
  "auth_enabled": true,
  "db_ok": true,
  "uptime_seconds": 5.2
}
```

---

## Étape 7 — Migrer depuis Fly.io

Si tu as des webhooks ou des clés existants sur Fly.io, migre la base SQLite :

```bash
# Sur ta machine locale (flyctl doit être installé)
export ORACLE_HOST=<oracle-ip>
bash infra/oracle/migrate-from-fly.sh
```

Le script :
1. Exporte la DB depuis Fly.io via SFTP
2. Vérifie l'intégrité SQLite
3. Copie la DB sur Oracle Cloud
4. Redémarre le service

---

## Étape 8 — Supprimer l'app Fly.io

Une fois que tout fonctionne sur Oracle :

```bash
# Vérifier que la migration est OK
curl https://notiftk.duckdns.org/api/watches

# Supprimer l'app Fly.io
fly apps destroy notiftk

# Optionnel : supprimer le volume
fly volumes list
fly volumes destroy <volume-id>
```

---

## Mise à jour du code

Pour déployer une nouvelle version :

```bash
# Depuis la VM Oracle
bash /opt/notiftk/infra/oracle/deploy.sh
```

Ou manuellement :
```bash
cd /opt/notiftk
git pull
.venv/bin/pip install -r requirements.txt --quiet
sudo systemctl restart notiftk
```

---

## Commandes utiles

```bash
# Logs app
sudo journalctl -u notiftk -f
sudo journalctl -u notiftk -n 100

# Logs Caddy (accès HTTP)
sudo journalctl -u caddy -f
cat /var/log/caddy/access.log

# Redémarrer
sudo systemctl restart notiftk
sudo systemctl restart caddy

# Base de données
sqlite3 /data/notiftk/webhooks.db ".tables"
sqlite3 /data/notiftk/webhooks.db "SELECT count(*) FROM api_keys;"

# Ressources (Always Free — surveiller)
free -h          # mémoire
df -h /data      # espace disque
```

---

## Garantie Always Free

Oracle Cloud garantit que les ressources suivantes ne sont **jamais facturées** :

| Ressource | Limite Always Free |
|---|---|
| VM.Standard.A1.Flex | 4 OCPUs + 24 GB RAM au total |
| Block Volume | 200 GB au total |
| Outbound data | 10 TB/mois |
| Object Storage | 20 GB |

Tant que tu restes dans ces limites, **ta CB ne sera jamais débitée** même si elle est enregistrée. Oracle envoie un email d'avertissement avant toute facturation.

> Pour être sûr : dans la console Oracle, **Budget** → créer une alerte à 1 € → tu recevras un email si tu approches d'une dépense.

---

## Dépannage

**L'app ne démarre pas**
```bash
sudo journalctl -u notiftk -n 50 --no-pager
# Vérifier le .env
cat /opt/notiftk/.env
```

**HTTPS ne fonctionne pas / certificat invalide**
```bash
sudo journalctl -u caddy -n 50 --no-pager
# Vérifier que le DNS pointe bien vers ton IP
nslookup notiftk.duckdns.org
# Vérifier que les ports 80/443 sont ouverts
curl -v http://notiftk.duckdns.org/health
```

**Ports bloqués malgré la Security List Oracle**
```bash
# Oracle Cloud bloque aussi en iptables — vérifier
sudo iptables -L INPUT --line-numbers | grep -E "80|443"
# Si absent, ajouter manuellement
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

**DuckDNS IP décalée après reboot**
> L'IP publique Oracle peut changer si l'instance est stoppée/redémarrée. Utilise une [Réservation d'IP publique](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm) Oracle pour fixer l'IP, ou mets à jour DuckDNS automatiquement :
```bash
# Crontab — mettre à jour DuckDNS toutes les 5 minutes
crontab -e
*/5 * * * * curl -sf "https://www.duckdns.org/update?domains=notiftk&token=TON_TOKEN&ip=" > /dev/null
```
