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

The installer asks for the MariaDB host, port, user and password, checks that it can log in, and generates an API key. At the end it prints the API's address and the key.

To install without prompts, for example from the Proxmox host with `pct exec <CTID> -- bash -c '...'`, pass the settings on the `bash` side of the pipe:

```bash
curl -fsSL https://raw.githubusercontent.com/AlbibiG/Fenlight/main/api/dist/fenlight-api-installer.sh | DB_HOST=192.168.1.10 FENLIGHT_DB_USER=fenlight FENLIGHT_DB_PASS='choose-a-password' bash
```

| Setting | Default | Meaning |
|---|---|---|
| `DB_HOST` | | MariaDB server address |
| `DB_PORT` | `3306` | MariaDB port |
| `FENLIGHT_DB_USER`, `FENLIGHT_DB_PASS` | | MariaDB login |
| `API_SECRET_KEY` | generated | Key Kodi must send |
| `API_PORT` | `8000` | Port the API listens on |
| `INSTALL_DIR` | `/opt/api` | Where the API is installed |
| `SERVICE_NAME` | `fenlight-api` | Name of the systemd service |

### 3. Connect Fen Light

In Fen Light's settings, under **Watched Indicators**, set **Watched Status Provider** to **MariaDB API**. Then fill in the **MariaDB Database and API** section:

- **External Host**: exactly the address the installer printed, for example `http://192.168.1.20:8000`, with no `/api` or trailing slash
- **API Key**: the key the installer printed
- **Database**: the database you created (`fenlight`)
- **Profile Name**: any name; each profile keeps its own watch history

### Updating, changing settings and removing

- **Update:** run the install command again. It keeps your settings.
- **Change settings:** add `--reconfigure`, as in `curl -fsSL <installer URL> | bash -s -- --reconfigure`. Or edit `/etc/fenlight-api/api.env` and run `systemctl restart fenlight-api`.
- **New API key**, for example if the old one leaked: add `--new-key`, as in `curl -fsSL <installer URL> | bash -s -- --new-key`. The installer prints the new key, and the old one stops working right away, so update **API Key** in Fen Light on every device. To choose the key yourself, put `API_SECRET_KEY='your-key'` just before `bash`.
- **Remove:**

  ```bash
  systemctl disable --now fenlight-api
  rm -rf /opt/api /etc/fenlight-api /etc/systemd/system/fenlight-api.service /var/log/fenlight-api-install.log
  systemctl daemon-reload && userdel fenlight-api
  ```

### Logs and troubleshooting

- **API log:** `journalctl -u fenlight-api -f`. Every Kodi request shows up with its status code, and errors include the full message.
- **Install log:** `/var/log/fenlight-api-install.log` has the output of the installer's package step.
- **Health check:** `http://<API address>/health` answers with status `ok` when the API is running and can log in to MariaDB. Uptime monitors can use it too.

What the status codes in the log mean:

- **401**: the **API Key** in Fen Light doesn't match `API_SECRET_KEY` in `/etc/fenlight-api/api.env`.
- **404**: the database in Fen Light's **Database** setting doesn't exist, or the API's MariaDB user can't access it.
- **503**: the API can't log in to MariaDB.

### Security

- The API key gives access to every database the API's MariaDB user can use, so give the API a dedicated user like the one above rather than `root`.
- The API uses plain HTTP. That's fine on a home network; if it's reachable from the internet, put an HTTPS reverse proxy such as Caddy or nginx in front of it.
