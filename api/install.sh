#!/usr/bin/env bash
# Installs the Fenlight API on a Debian/Ubuntu LXC container or VM as a systemd service.
# Run from the directory containing the app's *.py files and requirements.txt
# (the self-extracting installer built by build.sh does this for you).
#
# Usage: install.sh [--reconfigure]
#   Running it again updates the app and keeps the settings; --reconfigure asks for them again.
#
# Settings may be passed as environment variables; any that are missing are asked for:
#   DB_HOST, FENLIGHT_DB_USER, FENLIGHT_DB_PASS   MariaDB login (required)
#   DB_PORT         default 3306
#   API_SECRET_KEY  key Kodi sends as X-API-Key (generated if not given)
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
APT_LOG="/var/log/${SERVICE_NAME}-install.log"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: bash fenlight-api-installer.sh [--reconfigure]
       curl -fsSL <installer URL> | bash -s -- [--reconfigure]
Running it again updates the API and keeps its settings; --reconfigure asks for them again.
Settings can also be passed as environment variables: DB_HOST, DB_PORT, FENLIGHT_DB_USER,
FENLIGHT_DB_PASS, API_SECRET_KEY, API_PORT, INSTALL_DIR and SERVICE_NAME.
EOF
}

# /dev/tty passes a -r test even when there is no terminal, so check by opening it
have_tty() { { : </dev/tty; } 2>/dev/null; }

# ask VAR PROMPT [secret] [default]: keep $VAR if it's already set, otherwise ask on the terminal
# (through /dev/tty, so this works under `curl ... | bash`). Pressing Enter takes the default.
ask() {
    local var="$1" prompt="$2" secret="${3:-}" default="${4:-}" value="${!1:-}"
    if [[ -z "$value" ]] && have_tty; then
        if [[ -n "$secret" ]]; then
            read -rsp "$prompt: " value </dev/tty; echo >/dev/tty
        else
            [[ -z "$default" ]] || prompt+=" [${default}]"
            read -rp "$prompt: " value </dev/tty
        fi
    fi
    value="${value:-$default}"
    [[ -n "$value" ]] || die "$var is not set and there is no terminal to ask for it"
    printf -v "$var" '%s' "$value"
}

# env_line KEY VALUE: KEY="VALUE", escaped so systemd's EnvironmentFile parser reads VALUE back unchanged
env_line() { printf '%s="%s"\n' "$1" "$(printf '%s' "$2" | sed 's/[\\"`$]/\\&/g')"; }

# env_get KEY: KEY's value in the existing $ENV_FILE, written by env_line or (unquoted) by older installers
env_get() {
    local line value=""
    while IFS= read -r line; do
        if [[ "$line" == "$1="* ]]; then value="${line#*=}"; break; fi
    done <"$ENV_FILE"
    if [[ "$value" == \"*\" ]]; then
        value="$(printf '%s' "${value:1:${#value}-2}" | sed 's/\\\([\\"`$]\)/\1/g')"
    fi
    printf '%s' "$value"
}

# apt passes dpkg's error messages through its normal output, so keep all of it in a log and show its end on failure
apt_get() {
    if ! apt-get -o DPkg::Lock::Timeout=300 "$@" </dev/null >>"$APT_LOG" 2>&1; then
        tail -n 25 "$APT_LOG" >&2
        die "apt-get $1 failed; the full output is in ${APT_LOG}"
    fi
}

# check_db: log in to MariaDB with the collected settings and list the databases the user can use
check_db() {
    [[ "$DB_PORT" =~ ^[0-9]+$ ]] || { warn "the MariaDB port must be a number, not '${DB_PORT}'"; return 1; }
    log "Testing the MariaDB login ${FENLIGHT_DB_USER}@${DB_HOST}:${DB_PORT}"
    DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" FENLIGHT_DB_USER="$FENLIGHT_DB_USER" FENLIGHT_DB_PASS="$FENLIGHT_DB_PASS" \
        "$INSTALL_DIR/venv/bin/python" - <<'PY'
import os, sys
import mysql.connector
try:
    conn = mysql.connector.connect(host=os.environ['DB_HOST'], port=int(os.environ['DB_PORT']),
                                   user=os.environ['FENLIGHT_DB_USER'], password=os.environ['FENLIGHT_DB_PASS'],
                                   connection_timeout=10)
except mysql.connector.Error as exc:
    sys.exit('    Login failed: %s' % exc)
cursor = conn.cursor()
cursor.execute('SHOW DATABASES')
names = [row[0].decode() if isinstance(row[0], (bytes, bytearray)) else row[0] for row in cursor.fetchall()]
names = [name for name in names if name not in ('information_schema', 'performance_schema', 'mysql', 'sys')]
conn.close()
print('    Login OK. Databases this user can use: %s' % (', '.join(names) or 'none yet'))
if not names:
    print('    Create the database before connecting Kodi (see the README).')
PY
}

# configure: collect the settings (the current ones are the defaults), test the login, and write $ENV_FILE
configure() {
    local cur_host="" cur_port="" cur_user="" cur_pass="" cur_key="" cur_api_port=""
    if [[ -f "$ENV_FILE" ]]; then
        cur_host="$(env_get DB_HOST)"; cur_port="$(env_get DB_PORT)"; cur_user="$(env_get FENLIGHT_DB_USER)"
        cur_pass="$(env_get FENLIGHT_DB_PASS)"; cur_key="$(env_get API_SECRET_KEY)"; cur_api_port="$(env_get API_PORT)"
    fi
    while :; do
        ask DB_HOST "MariaDB host" "" "$cur_host"
        ask DB_PORT "MariaDB port" "" "${cur_port:-3306}"
        ask FENLIGHT_DB_USER "MariaDB user for the API" "" "$cur_user"
        ask FENLIGHT_DB_PASS "MariaDB password${cur_pass:+ (Enter keeps the current one)}" secret "$cur_pass"
        if check_db; then break; fi
        have_tty || die "could not log in to MariaDB with these settings"
        echo "Please try again; press Enter to keep a value shown in [brackets]." >/dev/tty
        cur_host="$DB_HOST"; cur_port="$DB_PORT"; cur_user="$FENLIGHT_DB_USER"
        DB_HOST=""; DB_PORT=""; FENLIGHT_DB_USER=""; FENLIGHT_DB_PASS=""
    done
    if [[ "$FENLIGHT_DB_USER" == root ]]; then
        warn "the API will log in as MariaDB's root user; a user limited to the Fen Light database is safer (see the README)"
    fi

    API_PORT="${API_PORT:-${cur_api_port:-8000}}"
    [[ "$API_PORT" =~ ^[0-9]+$ ]] || die "API_PORT must be a number, not '${API_PORT}'"
    API_SECRET_KEY="${API_SECRET_KEY:-$cur_key}"
    if [[ -z "$API_SECRET_KEY" ]]; then
        API_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        NEW_KEY=1
    fi

    mkdir -p "$ENV_DIR"
    (umask 077
     { env_line DB_HOST "$DB_HOST"
       env_line DB_PORT "$DB_PORT"
       env_line FENLIGHT_DB_USER "$FENLIGHT_DB_USER"
       env_line FENLIGHT_DB_PASS "$FENLIGHT_DB_PASS"
       env_line API_SECRET_KEY "$API_SECRET_KEY"
       env_line API_PORT "$API_PORT"
     } >"$ENV_FILE")
}

# api_health: the HTTP status of the API's /health check, or 000 while nothing answers yet
api_health() {
    python3 - "http://127.0.0.1:${API_PORT}/health" 2>/dev/null <<'PY' || echo 000
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=10).status)
except urllib.error.HTTPError as exc:
    print(exc.code)
PY
}

RECONFIGURE=""
case "${1:-}" in
    "") ;;
    --reconfigure) RECONFIGURE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option '$1'" ;;
esac

[[ $EUID -eq 0 ]] || die "run as root"
command -v apt-get >/dev/null || die "only Debian and Ubuntu (apt) systems are supported"
[[ -d /run/systemd/system ]] || die "systemd isn't running here. Use an LXC container or a VM; Docker and Podman containers aren't supported"

log "Installing system packages (output in ${APT_LOG})"
export DEBIAN_FRONTEND=noninteractive
: >"$APT_LOG"
apt_get update
# python3-venv brings pip into the virtualenv; recommended packages would add a compiler the API doesn't need
apt_get install -y --no-install-recommends python3 python3-venv ca-certificates
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || die "Python 3.10 or newer is needed (Debian 12+ or Ubuntu 22.04+); this system has $(python3 -V 2>&1)"

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

NEW_KEY=""
if [[ -f "$ENV_FILE" && -z "$RECONFIGURE" ]]; then
    log "Keeping the existing settings in ${ENV_FILE} (run with --reconfigure to change them)"
    API_PORT="$(env_get API_PORT)"
    API_PORT="${API_PORT:-8000}"
else
    log "Configuring"
    configure
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
status=000
for _ in $(seq 1 20); do
    status="$(api_health)"
    [[ "$status" == 000 ]] || break
    sleep 1
done

if [[ "$status" == 200 ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
    log "Done. The API is running at http://${ip:-<IP address>}:${API_PORT}"
    echo "    In Fen Light, set Watched Status Provider to 'MariaDB API' and External Host to that address."
    if [[ -n "$NEW_KEY" ]]; then
        echo "    API Key (generated): ${API_SECRET_KEY}"
    else
        echo "    API Key: API_SECRET_KEY in ${ENV_FILE}"
    fi
    echo "    Logs: journalctl -u ${SERVICE_NAME} -f"
    exit 0
fi

journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
[[ "$status" != 503 ]] || die "the API is running but can't log in to MariaDB; see the log above, then fix the settings with --reconfigure"
systemctl --no-pager status "${SERVICE_NAME}.service" || true
die "the service did not start; see the log above"
