#!/bin/sh
# CI: start Cookbook Collection and Dinner Log side by side on loopback for tests/contract/, each with a fresh
# data directory and freshly generated tokens, wired to each other as docs/seam.md (in cookbook-collection)
# describes. Waits until both answer /healthz.
#
#   sh .github/scripts/start-seam.sh CATALOG_CHECKOUT CATALOG_VENV DINNER_VENV
#
# The catalog runs with LAN_EDIT=1 so the tests can use its /admin API from loopback. Dinner Log must not ride
# on that: it reaches the catalog as http://catalog.test:8767 (added to /etc/hosts as 127.0.0.1), a Host name
# that isn't in the catalog's LAN_HOSTS, so its /admin/api/pick calls only work with COOKBOOK_TOKEN, as in
# production where LAN_EDIT is off.
#
# Appends the CONTRACT_* settings tests/contract/ reads to $GITHUB_ENV (or prints them when that isn't
# set). Server logs: $SEAM_DIR/catalog.log and $SEAM_DIR/dinner.log.
set -eu

CATALOG_SRC=$(cd "${1:?catalog checkout}" && pwd)
CATALOG_VENV=${2:?catalog venv}
DINNER_VENV=${3:?dinner-log venv}
DINNER_SRC=$(cd "$(dirname "$0")/../.." && pwd)
SEAM_DIR=${SEAM_DIR:-$(mktemp -d)}
CATALOG_URL=http://127.0.0.1:8767        # loopback by IP: an editor under LAN_EDIT=1
CATALOG_API_URL=http://catalog.test:8767  # loopback by a name outside LAN_HOSTS: what Dinner Log uses
DINNER_URL=http://127.0.0.1:8000

if ! getent hosts catalog.test >/dev/null; then
    if [ "$(id -u)" = 0 ]; then
        echo "127.0.0.1 catalog.test" >> /etc/hosts
    else
        echo "127.0.0.1 catalog.test" | sudo tee -a /etc/hosts >/dev/null
    fi
fi

token() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
READ_TOKEN=$(token)
HISTORY_TOKEN=$(token)
if [ -n "${GITHUB_ACTIONS:-}" ]; then   # keep them out of the job log
    echo "::add-mask::$READ_TOKEN"
    echo "::add-mask::$HISTORY_TOKEN"
fi
mkdir -p "$SEAM_DIR/catalog-data" "$SEAM_DIR/dinner-data"

# Cookbook Collection: as its installer runs it (gunicorn --preload --workers 2), seeded from seed.json.
# LAN_EDIT=1 lets requests from loopback use /admin/api; READ_TOKEN is what Dinner Log's picks present;
# DINNERLOG_* is where it pulls cooking history from.
env DATA_DIR="$SEAM_DIR/catalog-data" LAN_EDIT=1 READ_TOKEN="$READ_TOKEN" \
    DINNERLOG_API_URL="$DINNER_URL" DINNERLOG_TOKEN="$HISTORY_TOKEN" \
    nohup "$CATALOG_VENV/bin/gunicorn" --preload --workers 2 --threads 4 --bind 127.0.0.1:8767 \
    --chdir "$CATALOG_SRC" app:app > "$SEAM_DIR/catalog.log" 2>&1 &

# Dinner Log: as its installer runs it (one worker), pointed at the catalog's private address.
env DATA_DIR="$SEAM_DIR/dinner-data" APP_TZ=UTC COOKBOOK_URL="$CATALOG_API_URL" COOKBOOK_API_URL="$CATALOG_API_URL" \
    COOKBOOK_TOKEN="$READ_TOKEN" HISTORY_TOKEN="$HISTORY_TOKEN" \
    nohup "$DINNER_VENV/bin/gunicorn" -w 1 --threads 4 -b 127.0.0.1:8000 \
    --chdir "$DINNER_SRC" app:app > "$SEAM_DIR/dinner.log" 2>&1 &

for url in "$CATALOG_URL" "$DINNER_URL"; do
    i=0
    until curl -fsS -o /dev/null "$url/healthz" 2>/dev/null; do
        i=$((i + 1))
        if [ "$i" -ge 30 ]; then
            echo "$url/healthz didn't answer within 30 s" >&2
            tail -n 30 "$SEAM_DIR/catalog.log" "$SEAM_DIR/dinner.log" >&2
            exit 1
        fi
        sleep 1
    done
    echo "up: $url" >&2
done

{
    echo "CONTRACT_CATALOG_URL=$CATALOG_URL"
    echo "CONTRACT_CATALOG_API_URL=$CATALOG_API_URL"
    echo "CONTRACT_DINNER_URL=$DINNER_URL"
    echo "CONTRACT_DINNER_DATA_DIR=$SEAM_DIR/dinner-data"
    echo "SEAM_DIR=$SEAM_DIR"
} >> "${GITHUB_ENV:-/dev/stdout}"
