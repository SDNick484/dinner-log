#!/bin/sh
# Build the one-file installer: dist/setup-dinner-log.sh (shell header + base64 tarball of the app).
# Copy it to the Alpine server and run it as root; it unpacks the app and runs deploy/install-alpine.sh:
#
#   sh deploy/build.sh
#   scp dist/setup-dinner-log.sh root@<server>:
#   DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh setup-dinner-log.sh
#
# OUT=<path> writes somewhere else. Needs python3, base64 and tar (GNU or busybox).
set -eu
cd "$(dirname "$0")/.."
OUT=${OUT:-dist/setup-dinner-log.sh}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$(dirname "$OUT")" "$STAGE/dinner-log"
cp -r app.py backfill_uids.py backfill_thumbs.py backup.py templates static deploy "$STAGE/dinner-log/"
find "$STAGE" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
# The payload is written with Python's tarfile, not tar: every entry is root's whatever account builds it
# (busybox tar, as on Alpine, has no --owner/--group), and it goes to a file rather than down a pipe, so a
# failure stops the build instead of leaving an installer with an empty payload.
python3 - "$STAGE/dinner-log" "$STAGE/payload.tgz" <<'PY'
import sys
import tarfile


def as_root(member):
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    return member


with tarfile.open(sys.argv[2], "w:gz") as tar:
    tar.add(sys.argv[1], arcname="dinner-log", filter=as_root)
PY
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
  base64 "$STAGE/payload.tgz"
} > "$STAGE/installer.sh"
sh -n "$STAGE/installer.sh"
# Read the payload back the way the header unpacks it; only an installer that passes reaches $OUT.
if ! sed -n '/^__PAYLOAD_BELOW__$/,$p' "$STAGE/installer.sh" | tail -n +2 | base64 -d | tar tzf - \
        | grep -x 'dinner-log/deploy/install-alpine.sh' >/dev/null; then
    echo "The payload doesn't unpack; nothing built." >&2
    exit 1
fi
chmod +x "$STAGE/installer.sh"
mv "$STAGE/installer.sh" "$OUT"
echo "Built $OUT"
