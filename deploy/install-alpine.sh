#!/bin/sh
# Install or upgrade Dinner Log on Alpine Linux (OpenRC) from this checkout.
#
#   DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh deploy/install-alpine.sh
#
# - installs python3, flask, pillow and gunicorn from Alpine packages
# - with DINNER_HEIC=1, also installs pillow-heif (HEIC photos) from its musl wheel into $APP_DIR/vendor
# - copies the app to /opt/dinner-log
# - keeps the database and photos in /var/lib/dinner-log (never touched on upgrade)
# - runs as an unprivileged "dinnerlog" user via an OpenRC service
# - backs up the database nightly (/etc/periodic/daily/dinner-log-backup), unless Cookbook
#   Collection's app-backup job on the same host already does
# Safe to re-run.
set -eu

[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }
command -v apk >/dev/null || { echo "This installer is for Alpine (apk)." >&2; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$SRC/app.py" ] || { echo "app.py not found in $SRC; run from a repository checkout." >&2; exit 1; }

APP_DIR=/opt/dinner-log
DATA_DIR=/var/lib/dinner-log
CONF=/etc/conf.d/dinner-log

echo "==> Installing packages"
apk add --no-cache python3 py3-flask py3-pillow py3-gunicorn py3-jwt py3-cryptography tzdata su-exec >/dev/null
GUNICORN="$(command -v gunicorn)"

echo "==> Creating user and directories"
addgroup -S dinnerlog 2>/dev/null || true
adduser -S -D -H -G dinnerlog -h "$DATA_DIR" -s /sbin/nologin dinnerlog 2>/dev/null || true
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "==> Copying app to $APP_DIR"
rm -rf "$APP_DIR/app.py" "$APP_DIR/backfill_uids.py" "$APP_DIR/backfill_thumbs.py" "$APP_DIR/backup.py" "$APP_DIR/templates" "$APP_DIR/static"
cp -r "$SRC/app.py" "$SRC/backfill_uids.py" "$SRC/backfill_thumbs.py" "$SRC/backup.py" "$SRC/templates" "$SRC/static" "$APP_DIR/"

if [ "${DINNER_HEIC:-}" = 1 ]; then
    # Optional: pillow-heif bundles libheif, so it isn't patched through apk; re-run with DINNER_HEIC=1 to
    # update it. Installed next to the app (not into the system Python) and without dependencies, so Alpine's
    # py3-pillow stays the only Pillow. Without DINNER_HEIC an existing vendor/ is kept as it is.
    echo "==> Installing HEIC support (pillow-heif)"
    if apk add --no-cache py3-pip >/dev/null 2>&1 \
        && pip install --quiet --no-cache-dir --break-system-packages --no-deps --only-binary=:all: \
            --target "$APP_DIR/vendor.new" -r "$SRC/deploy/requirements-heic.txt" \
        && PYTHONPATH="$APP_DIR/vendor.new" python3 -c "
import io, pillow_heif
from PIL import Image
pillow_heif.register_heif_opener()
b = io.BytesIO()
Image.new('RGB', (16, 16)).save(b, 'HEIF')
b.seek(0)
Image.open(b).load()
"; then
        rm -rf "$APP_DIR/vendor"
        mv "$APP_DIR/vendor.new" "$APP_DIR/vendor"
    else
        rm -rf "$APP_DIR/vendor.new"
        echo "Couldn't install a working pillow-heif (needs a musl wheel and Alpine's Pillow 11.1 or newer);" >&2
        echo "continuing without HEIC support." >&2
    fi
fi

chown -R root:root "$APP_DIR"
chown -R dinnerlog:dinnerlog "$DATA_DIR"
chmod 750 "$DATA_DIR"

if [ -n "${DINNER_TZ:-}" ] && [ ! -f "/usr/share/zoneinfo/$DINNER_TZ" ]; then
    echo "Unknown time zone '$DINNER_TZ' (expected an IANA name like America/Chicago)." >&2
    exit 1
fi

echo "==> Installing OpenRC service"
# Settings live in $CONF. On upgrade, existing values are kept; a variable
# given on the command line (DINNER_PORT, DINNER_BIND, DINNER_TZ) updates
# just that setting.
[ -f "$CONF" ] || : > "$CONF"
set_conf() {  # set_conf KEY VALUE COMMENT -- add KEY if missing, or replace its value
    if grep -q "^\(export \)\{0,1\}$1=" "$CONF"; then
        [ -n "$2" ] && sed -i "s|^\(export \)\{0,1\}$1=.*|\1$1=$2|" "$CONF"
    else
        printf '# %s\n%s=%s\n' "$3" "$1" "${2:-$4}" >> "$CONF"
    fi
    return 0
}
set_conf DINNER_PORT "${DINNER_PORT:-}" "Port Dinner Log listens on" 8000
set_conf DINNER_BIND "${DINNER_BIND:-}" \
    "Address to bind; use 127.0.0.1 when the reverse proxy/tunnel runs on this host" 0.0.0.0
set_conf "export DATA_DIR" "" "Database and photos" "$DATA_DIR"
if [ -d "$APP_DIR/vendor" ]; then
    set_conf "export PYTHONPATH" "$APP_DIR/vendor" "pillow-heif for HEIC photos (installed by DINNER_HEIC=1)" ""
fi
set_conf "export APP_TZ" "${DINNER_TZ:-}" "IANA time zone used for \"today\" (e.g. America/Chicago)" UTC
set_conf "export COOKBOOK_URL" "" "Cookbook Collection's public address, for links (optional)" ""
set_conf "export COOKBOOK_API_URL" "" "Cookbook Collection's private address, e.g. http://127.0.0.1:8767 (optional)" ""
set_conf "export COOKBOOK_TOKEN" "" "Cookbook Collection's READ_TOKEN, for Pick for tonight (optional)" ""
set_conf "export BACKUP_DIR" "" "Where nightly database snapshots go" /var/backups/dinner-log
set_conf "export BACKUP_KEEP_DAYS" "" "Days of nightly snapshots to keep" 14
set_conf "export BACKUP_TARGET" "" "Optional folder on other storage (e.g. an NFS mount) that also gets snapshots and photos" ""
set_conf "export CF_TEAM_DOMAIN" "" "Cloudflare Access team domain, e.g. yourteam.cloudflareaccess.com (with CF_AUD: verify sign-ins)" ""
set_conf "export CF_AUD" "" "AUD tag of the Access application in front of Dinner Log (set both or neither)" ""
set_conf "export LAN_ACCESS" "" "1 = with Access verification on, also let in direct requests from the local network" 0
set_conf "export LAN_CIDRS" "" "Networks LAN_ACCESS allows (empty = loopback + private ranges)" ""
set_conf "export LAN_HOSTS" "" "Host names LAN_ACCESS accepts besides IP addresses, e.g. dinners.lan" ""
sh "$SRC/deploy/history-token.sh" "$CONF"  # made once if missing or empty; never replaced
chmod 600 "$CONF"  # holds COOKBOOK_TOKEN and HISTORY_TOKEN; OpenRC reads it as root

echo "==> Installing nightly backup"
# Always installed: on a host where Cookbook Collection's app-backup runs, the job skips itself each night,
# so either install order works and nothing is backed up twice (or not at all if app-backup goes away).
CONF_DATA_DIR=$(. "$CONF"; echo "$DATA_DIR")
BACKUP_DIR=$(. "$CONF"; echo "${BACKUP_DIR:-/var/backups/dinner-log}")
mkdir -p "$BACKUP_DIR"
chown dinnerlog:dinnerlog "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
mkdir -p /etc/periodic/daily
install -m 755 "$SRC/deploy/dinner-log-backup" /etc/periodic/daily/dinner-log-backup
rc-update add crond default >/dev/null 2>&1 || true
rc-service crond start >/dev/null 2>&1 || true
if [ -e /etc/periodic/daily/app-backup ]; then
    BACKUP_NOTE="Backups: Cookbook Collection's app-backup covers this host (Dinner Log's job skips itself)"
elif [ -f "$CONF_DATA_DIR/meals.db" ]; then
    echo "==> Backing up the database before restarting"
    /etc/periodic/daily/dinner-log-backup \
        || echo "    (backup reported a problem; see /var/log/dinner-log-backup.log)" >&2
    BACKUP_NOTE="Backups: nightly to $BACKUP_DIR (log: /var/log/dinner-log-backup.log)"
else
    BACKUP_NOTE="Backups: nightly to $BACKUP_DIR (log: /var/log/dinner-log-backup.log)"
fi

cat > /etc/init.d/dinner-log <<INITEOF
#!/sbin/openrc-run
name="dinner-log"
description="Dinner Log (household meal tracker)"
command="$GUNICORN"
command_args="-b \${DINNER_BIND:-0.0.0.0}:\${DINNER_PORT:-8000} -w 1 --threads 4 --chdir $APP_DIR app:app"
command_user="dinnerlog:dinnerlog"
command_background=true
pidfile="/run/dinner-log.pid"
output_log="/var/log/dinner-log.log"
error_log="/var/log/dinner-log.log"

depend() {
    need net
}

start_pre() {
    checkpath -f -o dinnerlog:dinnerlog /var/log/dinner-log.log
}
INITEOF
chmod +x /etc/init.d/dinner-log

rc-update add dinner-log default >/dev/null 2>&1 || true
rc-service dinner-log restart >/dev/null 2>&1 || rc-service dinner-log start

# shellcheck disable=SC1090
. "$CONF"
PORT="${DINNER_PORT:-8000}"

echo "==> Checking that it came up"
i=0
until wget -q -O /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge 15 ]; then
        echo "Service did not respond. Last log lines:" >&2
        tail -n 20 /var/log/dinner-log.log >&2 || true
        exit 1
    fi
    sleep 1
done

IP="$(ip -4 -o addr show 2>/dev/null | awk '$2 != "lo" {print $4}' | cut -d/ -f1 | head -n1)"
echo
echo "Dinner Log is running: http://${IP:-<host-ip>}:$PORT"
echo "Data: $CONF_DATA_DIR (database + photos)"
echo "$BACKUP_NOTE"
[ -n "${BACKUP_TARGET:-}" ] || echo "  Photos are only backed up when BACKUP_TARGET is set (see README > Backups)"
echo "Logs: /var/log/dinner-log.log"
echo "Cookbook Collection reads cooking history with HISTORY_TOKEN: set its DINNERLOG_TOKEN to the same value"
echo "  (grep HISTORY_TOKEN $CONF)"
