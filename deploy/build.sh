#!/bin/sh
# Build the one-file installer: dist/setup-dinner-log.sh (shell header + base64 tarball of the app).
# Copy it to the Alpine server and run it as root; it unpacks the app and runs deploy/install-alpine.sh:
#
#   sh deploy/build.sh
#   scp dist/setup-dinner-log.sh root@<server>:
#   DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh setup-dinner-log.sh
#
# OUT=<path> writes somewhere else.
set -eu
cd "$(dirname "$0")/.."
OUT=${OUT:-dist/setup-dinner-log.sh}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$(dirname "$OUT")" "$STAGE/dinner-log"
cp -r app.py templates static deploy "$STAGE/dinner-log/"
find "$STAGE" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
{
  cat <<'HEAD'
#!/bin/sh
# Dinner Log installer for Alpine Linux (OpenRC): unpacks the app and runs deploy/install-alpine.sh.
#   DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh setup-dinner-log.sh
# Safe to re-run: the database, photos and /etc/conf.d/dinner-log are kept.
set -eu
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed -n '/^__PAYLOAD_BELOW__$/,$p' "$0" | tail -n +2 | base64 -d | tar xz -C "$TMP"
sh "$TMP/dinner-log/deploy/install-alpine.sh"
exit 0
HEAD
  echo "__PAYLOAD_BELOW__"
  tar --owner=0 --group=0 -C "$STAGE" -czf - dinner-log | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT"
