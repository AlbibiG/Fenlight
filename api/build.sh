#!/usr/bin/env bash
# Builds dist/fenlight-api-installer.sh: a single self-extracting script that
# contains the app, requirements.txt and install.sh. No secrets are bundled.
#
# Usage on a fresh container (as root):
#   bash fenlight-api-installer.sh
#   curl -fsSL http://<host>/fenlight-api-installer.sh | bash
#   DB_HOST=... FENLIGHT_DB_USER=... FENLIGHT_DB_PASS=... bash fenlight-api-installer.sh   # non-interactive
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PKG_DIR/app"
OUT="$PKG_DIR/dist/fenlight-api-installer.sh"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp "$APP_DIR"/*.py "$PKG_DIR/requirements.txt" "$PKG_DIR/install.sh" "$stage/"
mkdir -p "$PKG_DIR/dist"
{
    cat <<'HEADER'
#!/usr/bin/env bash
# Self-extracting Fenlight API installer. Run as root on a Debian/Ubuntu container.
# Everything is inside main() so `curl ... | bash` reads the whole script before running it.
set -euo pipefail
main() {
    # not local: the EXIT trap fires after main returns
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    base64 -d <<'PAYLOAD' | tar -xz -C "$tmp"
HEADER
    tar -czf - -C "$stage" . | base64 -w 76
    cat <<'FOOTER'
PAYLOAD
    bash "$tmp/install.sh" "$@"
}
main "$@"
FOOTER
} >"$OUT"
chmod +x "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
