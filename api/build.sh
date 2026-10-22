#!/usr/bin/env bash
# Builds dist/fenlight-api-installer.sh: a single self-extracting script that
# contains the app, requirements.txt and install.sh. No secrets are bundled.
#
# Usage on a Debian/Ubuntu LXC container or VM (as root), see the README for details:
#   curl -fsSL https://raw.githubusercontent.com/AlbibiG/Fenlight/main/api/dist/fenlight-api-installer.sh | bash
#   ... | bash -s -- --reconfigure                                    # change the settings later
#   ... | bash -s -- --new-key                                        # replace the API key
#   ... | DB_HOST=... FENLIGHT_DB_USER=... FENLIGHT_DB_PASS=... bash   # no prompts
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PKG_DIR/app"
OUT="$PKG_DIR/dist/fenlight-api-installer.sh"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp "$APP_DIR"/*.py "$PKG_DIR/requirements.txt" "$PKG_DIR/install.sh" "$stage/"
# a Windows checkout can have CRLF line endings, which bash on Linux can't run
sed -i 's/\r$//' "$stage"/*
mkdir -p "$PKG_DIR/dist"
{
    cat <<'HEADER'
#!/usr/bin/env bash
# Self-extracting Fenlight API installer. Run as root on a Debian/Ubuntu LXC container or VM.
# Options: --reconfigure to change the settings of an existing install, --help for more.
# Everything is inside main() so `curl ... | bash` reads the whole script before running it.
set -euo pipefail
main() {
    # not local: the EXIT trap fires after main returns
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    base64 -d <<'PAYLOAD' | tar -xz -C "$tmp"
HEADER
    tar -czf - --owner=0 --group=0 --numeric-owner -C "$stage" . | base64 -w 76
    cat <<'FOOTER'
PAYLOAD
    bash "$tmp/install.sh" "$@"
}
main "$@"
FOOTER
} >"$OUT"
chmod +x "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
