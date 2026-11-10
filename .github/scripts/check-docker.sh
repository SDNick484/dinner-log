#!/bin/sh
# CI: run the Docker image on a fresh data folder and check what the README promises: the image's own
# HEALTHCHECK passes, a dinner with a photo is stored with its thumbnail, the files belong to uid 10001,
# the app doesn't run as root, and HEIC support (the default HEIC=1 build) is really there.
#
#   sh .github/scripts/check-docker.sh IMAGE
set -eu

IMAGE=${1:?usage: check-docker.sh IMAGE}
NAME=dinner-log-ci
PORT=18000
URL="http://127.0.0.1:$PORT"
WORK=$(mktemp -d)
# Owned by the runner's user, like a fresh ./data on a host: the entrypoint must chown it to 10001.
DATA="$WORK/data"
mkdir "$DATA"

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
fail() {
    printf '   FAIL: %s\n' "$*" >&2
    exit 1
}
expect() {  # expect WHAT ACTUAL WANTED
    [ "$2" = "$3" ] || fail "$1: got '$2', want '$3'"
    ok "$1: $3"
}
finish() {
    status=$?
    if [ "$status" != 0 ]; then
        echo "--- container log" >&2
        docker logs "$NAME" >&2 2>&1 || true
    fi
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap finish EXIT

step "Start on an empty data folder"
# --health-interval keeps the image's HEALTHCHECK command but runs it every 2 s instead of every 60 s.
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8000" -v "$DATA:/data" --health-interval 2s "$IMAGE" >/dev/null
i=0
while :; do
    health=$(docker inspect -f '{{.State.Health.Status}}' "$NAME")
    [ "$health" = healthy ] && break
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || fail "container exited"
    i=$((i + 1))
    [ "$i" -lt 60 ] || fail "not healthy after 60 s (status: $health)"
    sleep 1
done
ok "HEALTHCHECK reports healthy"
errors=$(docker logs "$NAME" 2>&1 | grep -F '[ERROR]' || true)
[ -z "$errors" ] || fail "errors in the container log: $errors"
ok "no [ERROR] lines in the container log"

step "Not root"
expect "PID 1 uid/gid" "$(docker exec "$NAME" awk '/^Uid:/ {u=$2} /^Gid:/ {g=$2} END {print u ":" g}' /proc/1/status)" \
    "10001:10001"
expect "PID 1 is gunicorn" "$(docker exec "$NAME" sh -c 'tr "\0" " " < /proc/1/cmdline' | grep -c gunicorn)" 1
# [g]unicorn: the exec'd shell's own command line holds this script, and must not match itself.
others=$(docker exec "$NAME" sh -c 'for d in /proc/[0-9]*; do
    tr "\0" " " < "$d/cmdline" 2>/dev/null | grep -q "bin/[g]unicorn" || continue
    awk "/^Uid:/ {print \$2}" "$d/status"; done' | sort -u)
expect "uids of all gunicorn processes" "$others" 10001

step "HEIC support in the default build"
docker exec "$NAME" python -c "import pillow_heif" || fail "pillow_heif isn't importable in the image"
ok "pillow_heif imports"

step "A dinner with a photo"
docker exec "$NAME" python -c 'import sys; from PIL import Image
Image.new("RGB", (2400, 1800), (200, 60, 40)).save(sys.stdout.buffer, "JPEG")' > "$WORK/dinner.jpg"
code=$(curl -sS -o /dev/null -D "$WORK/headers" -w '%{http_code}' -F 'name=CI dinner' -F source_type=original \
    -F "photos=@$WORK/dinner.jpg;type=image/jpeg" "$URL/new")
expect "POST /new" "$code" 302
location=$(tr -d '\r' < "$WORK/headers" | grep -i '^location:' | cut -d' ' -f2-)
case "$location" in
    *skipped=*) fail "the photo was skipped: $location" ;;
    */meal/*) ok "redirected to $location" ;;
    *) fail "unexpected redirect: $location" ;;
esac
photo=$(find "$DATA/photos" -type f -name '*.jpg' ! -name '*-t.jpg' -exec basename {} \;)
expect "photos stored" "$(printf '%s\n' "$photo" | grep -c .)" 1
thumb="${photo%.jpg}-t.jpg"
[ -f "$DATA/photos/$thumb" ] || fail "no thumbnail $thumb beside $photo"
ok "thumbnail $thumb"
expect "owners of the data folder, database and photos" \
    "$(stat -c '%u:%g' "$DATA" "$DATA/meals.db" "$DATA/photos" "$DATA/photos/$photo" "$DATA/photos/$thumb" | sort -u)" \
    "10001:10001"

printf '\nAll Docker checks passed.\n'
