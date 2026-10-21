#!/bin/sh
# Install or upgrade Dinner Log on Alpine Linux (OpenRC) from this checkout.
#
#   DINNER_PORT=8000 sh deploy/install-alpine.sh
#
# - installs python3, flask, pillow and gunicorn from Alpine packages
# - copies the app to /opt/dinner-log
# - keeps the database and photos in /var/lib/dinner-log (never touched on upgrade)
# - runs as an unprivileged "dinnerlog" user via an OpenRC service
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
apk add --no-cache python3 py3-flask py3-pillow py3-gunicorn >/dev/null
GUNICORN="$(command -v gunicorn)"

echo "==> Creating user and directories"
addgroup -S dinnerlog 2>/dev/null || true
adduser -S -D -H -G dinnerlog -h "$DATA_DIR" -s /sbin/nologin dinnerlog 2>/dev/null || true
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "==> Copying app to $APP_DIR"
rm -rf "$APP_DIR/app.py" "$APP_DIR/templates" "$APP_DIR/static"
cp -r "$SRC/app.py" "$SRC/templates" "$SRC/static" "$APP_DIR/"
chown -R root:root "$APP_DIR"
chown -R dinnerlog:dinnerlog "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> Installing OpenRC service"
# Keep an existing config on upgrade, unless DINNER_PORT is given explicitly.
if [ ! -f "$CONF" ] || [ -n "${DINNER_PORT:-}" ]; then
    cat > "$CONF" <<CONFEOF
# Port Dinner Log listens on
DINNER_PORT=${DINNER_PORT:-8000}
export DATA_DIR=$DATA_DIR
CONFEOF
fi

cat > /etc/init.d/dinner-log <<INITEOF
#!/sbin/openrc-run
name="dinner-log"
description="Dinner Log (household meal tracker)"
command="$GUNICORN"
command_args="-b 0.0.0.0:\${DINNER_PORT:-8000} -w 1 --threads 4 --chdir $APP_DIR app:app"
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
until wget -q -O /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; do
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
echo "Data: $DATA_DIR (database + photos; include it in backups)"
echo "Logs: /var/log/dinner-log.log"
