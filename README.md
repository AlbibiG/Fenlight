# FenlightAG
Just like the old Fenlight developed by TikiPeter and forked by AnonyMouse. Except this one allows you to connect your watch history to a personal database so you can access the data anywhere instead of relying on third-party providers. 

To use the queue functionality with Home Assistant, you have to install the script package as well. The script is not necessary if not using queue through Home Assistant.

## Watch history API

The API lets Fen Light keep its watch history in your own MariaDB database through a small web service. Kodi devices then only need the API's address and key, not a database login.

It runs on a Debian 12+ or Ubuntu 22.04+ LXC container or VM. It needs systemd, so Docker and Podman containers aren't supported. The database must be MariaDB 10.4 or newer; MySQL isn't supported.

### 1. Prepare MariaDB

On the MariaDB server, create the database and a user that can only use that database:

```sql
CREATE DATABASE fenlight CHARACTER SET utf8mb4;
CREATE USER 'fenlight'@'%' IDENTIFIED BY 'choose-a-password';
GRANT ALL PRIVILEGES ON fenlight.* TO 'fenlight'@'%';
```

`fenlight` is Fen Light's default database name. MariaDB also has to accept connections from the network: on Debian and Ubuntu, set `bind-address = 0.0.0.0` in `/etc/mysql/mariadb.conf.d/50-server.cnf`, then run `systemctl restart mariadb`.

### 2. Install the API

As root in the container or VM:

```bash
apt-get update && apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/AlbibiG/Fenlight/main/api/dist/fenlight-api-installer.sh | bash
```

The installer asks for the MariaDB host, port, user and password, checks that it can log in, and generates an API key named `default`. At the end it prints the API's address and the key.

To install without prompts, for example from the Proxmox host with `pct exec <CTID> -- bash -c '...'`, pass the settings on the `bash` side of the pipe:

```bash
curl -fsSL https://raw.githubusercontent.com/AlbibiG/Fenlight/main/api/dist/fenlight-api-installer.sh | DB_HOST=192.168.1.10 FENLIGHT_DB_USER=fenlight FENLIGHT_DB_PASS='choose-a-password' bash
```

| Setting | Default | Meaning |
|---|---|---|
| `DB_HOST` | | MariaDB server address |
| `DB_PORT` | `3306` | MariaDB port |
| `FENLIGHT_DB_USER`, `FENLIGHT_DB_PASS` | | MariaDB login |
| `API_SECRET_KEY` | generated | The API key named `default` |
| `API_PORT` | `8000` | Port the API listens on |
| `INSTALL_DIR` | `/opt/api` | Where the API is installed |
| `SERVICE_NAME` | `fenlight-api` | Name of the systemd service |

### 3. Connect Fen Light

In Fen Light's settings, under **Watched Indicators**, set **Watched Status Provider** to **MariaDB API**. Then fill in the **MariaDB Database and API** section:

- **External Host**: exactly the address the installer printed, for example `http://192.168.1.20:8000`, with no `/api` or trailing slash
- **API Key**: the key the installer printed
- **Database**: the database you created (`fenlight`)
- **Profile Name**: pick one that already has history, or choose **New Profile...** and type a name; each profile keeps its own watch history

Then press **Test Connection**. It checks the address, API key, database and profile, and says exactly what to fix if something is wrong.

To bring over the history you already have, press **Import Watch History**. It copies what Fen Light kept on this device (watched, in progress, hidden shows and personal lists) or its copy of your Trakt history into the database. Entries already there are kept, so importing twice is harmless.

If the API can't be reached, for example while its container restarts, Fen Light saves your changes and sends them once it's back.

### API keys

Give each Kodi device its own key, so you can lock one device out without changing the others. Manage them with `fenlight-api-keys` on the API's container or VM, as root:

```bash
fenlight-api-keys                                   # list every key with its name
fenlight-api-keys add "Living Room"                 # add a key and print it
fenlight-api-keys show "Living Room"                # print one key again
fenlight-api-keys reset "Living Room"               # new key for that device; its old one stops working
fenlight-api-keys rename "Living Room" "Lounge"
fenlight-api-keys remove "Lounge"                   # that device is locked out
```

Enter each device's key as **API Key** in Fen Light on that device. Changes apply at once, without restarting the API. `add` and `reset` take an optional key of your own after the name. Installs from before named keys keep working: the installer moves their one key into the list as `default`.

### Updating, changing settings and removing

- **Update:** run the install command again. It keeps your settings. [api/CHANGELOG.md](api/CHANGELOG.md) lists what changed in each update. After an update from an older version, the first request converts older tables so that lookups can use an index; with a long history this takes a few seconds, once.
- **Change settings:** add `--reconfigure`, as in `curl -fsSL <installer URL> | bash -s -- --reconfigure`. Or edit `/etc/fenlight-api/api.env` and run `systemctl restart fenlight-api`.
- **New API key**, for example if one leaked: `fenlight-api-keys reset NAME` replaces that device's key (see [API keys](#api-keys)). The installer's `--new-key` option does the same for the key named `default`; to choose that key yourself, put `API_SECRET_KEY='your-key'` just before `bash`.
- **Remove:**

  ```bash
  systemctl disable --now fenlight-api
  rm -rf /opt/api /etc/fenlight-api /etc/systemd/system/fenlight-api.service /var/log/fenlight-api-install.log /usr/local/bin/fenlight-api-keys
  systemctl daemon-reload && userdel fenlight-api
  ```

### Logs and troubleshooting

- **API log:** `journalctl -u fenlight-api -f`. Every Kodi request shows up with its status code, and errors include the full message.
- **Install log:** `/var/log/fenlight-api-install.log` has the output of the installer's package step.
- **Health check:** `http://<API address>/health` answers with status `ok` when the API is running and can log in to MariaDB. Uptime monitors can use it too.

**Test Connection** in Fen Light's settings is the quickest check. What the status codes in the log mean:

- **401**: the **API Key** in Fen Light doesn't match any key that `fenlight-api-keys` lists.
- **404**: the database in Fen Light's **Database** setting doesn't exist, or the API's MariaDB user can't access it.
- **503**: the API can't log in to MariaDB.

### Security

- Every API key gives access to every database the API's MariaDB user can use, so give the API a dedicated user like the one above rather than `root`.
- The keys are kept in `/etc/fenlight-api/keys.json`, readable only by root and the API's service user. Remove a device's key when you stop using that device.
- The API uses plain HTTP. That's fine on a home network; if it's reachable from the internet, put an HTTPS reverse proxy such as Caddy or nginx in front of it.

## Releasing

- **Add-on:** run `python release.py 1.1.31` (with the new version number), then commit and push. It sets the version in `addon.xml`, builds `packages/plugin.video.fenlight-1.1.31.zip` without Python caches or pymysql's tests, points `fen_light_version` and `packages/index.html` at it, and keeps the newest 10 zips for rollback.
- **API:** run `bash api/build.sh`, then commit the changed `api/` files together with the rebuilt `api/dist/fenlight-api-installer.sh`. Users get it by re-running the install command.
