#!/usr/bin/env bash
# Installs the Fenlight API onto a Debian/Ubuntu container as a systemd service.
# Run from the directory containing the app's *.py files and requirements.txt
# (the self-extracting installer built by build.sh does this for you).
#
# Settings may be passed as environment variables; anything missing is prompted for:
#   DB_HOST, FENLIGHT_DB_USER, FENLIGHT_DB_PASS   MariaDB/MySQL connection (required)
#   API_SECRET_KEY                        client X-Api-Key (generated if left blank)
#   API_PORT        default 8000
#   INSTALL_DIR     default /opt/api
#   SERVICE_NAME    default fenlight-api
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/api}"
SERVICE_NAME="${SERVICE_NAME:-fenlight-api}"
SERVICE_USER="fenlight-api"
ENV_DIR="/etc/${SERVICE_NAME}"
ENV_FILE="${ENV_DIR}/api.env"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Prompt via /dev/tty so this works under `curl ... | bash`.
ask() {
    local var="$1" prompt="$2" secret="${3:-}" default="${4:-}" value="${!1:-}"
    if [[ -z "$value" && ! -r /dev/tty && -n "$default" ]]; then
        value="$default"
    elif [[ -z "$value" ]]; then
        [[ -r /dev/tty ]] || die "$var is not set and no terminal is available to prompt for it"
        if [[ -n "$secret" ]]; then
            read -rsp "$prompt: " value </dev/tty; echo >/dev/tty
        else
            read -rp "$prompt: " value </dev/tty
        fi
        value="${value:-$default}"
    fi
    printf -v "$var" '%s' "$value"
}

[[ $EUID -eq 0 ]] || die "run as root"
command -v apt-get >/dev/null || die "only Debian/Ubuntu (apt) containers are supported"
command -v systemctl >/dev/null || die "systemd is required"

log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq </dev/null
apt-get install -y -qq python3 python3-venv python3-pip ca-certificates </dev/null >/dev/null

log "Creating service user ${SERVICE_USER}"
id "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"

log "Copying application to ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
install -m 0644 "$SRC_DIR"/*.py "$SRC_DIR/requirements.txt" "$INSTALL_DIR/"

log "Creating virtualenv and installing Python dependencies"
[[ -x "$INSTALL_DIR/venv/bin/python" ]] || python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip </dev/null
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt" </dev/null
chown -R root:root "$INSTALL_DIR"

if [[ -f "$ENV_FILE" ]]; then
    log "Keeping existing configuration in ${ENV_FILE}"
    API_PORT="$(sed -n 's/^API_PORT=//p' "$ENV_FILE")"
    API_PORT="${API_PORT:-8000}"
else
    log "Configuring"
    ask DB_HOST "Database host"
    ask FENLIGHT_DB_USER "Database user"
    ask FENLIGHT_DB_PASS "Database password" secret
    ask API_SECRET_KEY "API key for clients (blank = generate)" secret
    API_PORT="${API_PORT:-8000}"
    GENERATED_KEY=""
    if [[ -z "$API_SECRET_KEY" ]]; then
        API_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        GENERATED_KEY=1
    fi
    mkdir -p "$ENV_DIR"
    (umask 077; cat >"$ENV_FILE" <<EOF
DB_HOST=${DB_HOST}
FENLIGHT_DB_USER=${FENLIGHT_DB_USER}
FENLIGHT_DB_PASS=${FENLIGHT_DB_PASS}
API_SECRET_KEY=${API_SECRET_KEY}
API_PORT=${API_PORT}
EOF
    )
fi

log "Installing systemd unit ${SERVICE_NAME}.service"
cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Fenlight sync API (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn main:app --host 0.0.0.0 --port \${API_PORT}
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service" >/dev/null
systemctl restart "${SERVICE_NAME}.service"

log "Waiting for the API to answer on port ${API_PORT}"
for _ in $(seq 1 15); do
    if python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:${API_PORT}/docs', timeout=2)" 2>/dev/null; then
        log "Done. API running at http://$(hostname -I | awk '{print $1}'):${API_PORT}"
        [[ -n "${GENERATED_KEY:-}" ]] && echo "    Generated client API key: ${API_SECRET_KEY}"
        echo "    Config: ${ENV_FILE}   Logs: journalctl -u ${SERVICE_NAME} -f"
        exit 0
    fi
    sleep 1
done

systemctl --no-pager status "${SERVICE_NAME}.service" || true
journalctl -u "${SERVICE_NAME}" -n 30 --no-pager || true
die "service did not come up; check ${ENV_FILE} and the log above"
