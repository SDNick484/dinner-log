#!/bin/sh
# CI: check a Dinner Log that deploy/install-alpine.sh just installed on this (Alpine, OpenRC) host, then
# turn on the Access gate and rerun the installer. Run as root from the repository, after the installer:
#
#   sh .github/scripts/check-installed.sh [dist/setup-dinner-log.sh]
#
# DINNER_HEIC=1 also checks the HEIC support the installer was asked for. Needs curl. Test images are made
# with the Pillow (and pillow-heif) the installer chose. Stops at the first mismatch.
#
# No request here carries a Cf-Access-Jwt-Assertion header, and that header is the only thing that makes the
# app fetch Cloudflare's signing keys, so nothing leaves the host; the log check in check_access_gate() makes sure.
set -eu

INSTALLER=${1:-dist/setup-dinner-log.sh}
CONF=/etc/conf.d/dinner-log
APP_DIR=/opt/dinner-log
DATA_DIR=/var/lib/dinner-log
BACKUP_DIR=/var/backups/dinner-log
BACKUP_JOB=/etc/periodic/daily/dinner-log-backup
FAKE_APP_BACKUP=/etc/periodic/daily/app-backup
LOG=/var/log/dinner-log.log
PIDFILE=/run/dinner-log.pid
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"; rm -f "$FAKE_APP_BACKUP"' EXIT

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
setting() {  # setting NAME: its value as the service sees it (conf.d sourced in a subshell)
    # shellcheck source=/dev/null
    (. "$CONF" && eval "printf '%s' \"\${$1:-}\"")
}
set_setting() {  # set_setting NAME VALUE: edit an `export NAME=` line the installer wrote, as an admin would
    grep -q "^export $1=" "$CONF" || fail "no 'export $1=' line in $CONF"
    sed -i "s|^export $1=.*|export $1=$2|" "$CONF"
}
http() {  # http [curl args] URL: prints the status; body in $WORK/body, headers in $WORK/headers
    curl -sS -o "$WORK/body" -D "$WORK/headers" -w '%{http_code}' "$@"
}
header() {  # header NAME: that header from the last http call
    tr -d '\r' < "$WORK/headers" | grep -i "^$1:" | tail -n 1 | cut -d' ' -f2-
}
wait_healthy() {
    i=0
    until [ "$(http "$URL/healthz" 2>/dev/null || true)" = 200 ]; do
        i=$((i + 1))
        if [ "$i" -ge 30 ]; then
            tail -n 30 "$LOG" >&2 || true
            fail "/healthz didn't answer within 30 s"
        fi
        sleep 1
    done
}
restart() {
    if ! rc-service dinner-log restart >/dev/null; then
        ps -o pid,ppid,stat,user,args >&2 || true   # a Z in STAT means nothing reaped the old process
        fail "rc-service dinner-log restart"
    fi
    wait_healthy
}
not_dinnerlog() { find "$DATA_DIR" "$BACKUP_DIR" ! -user dinnerlog; }
py() { python3 -c "$@"; }

[ "$(id -u)" = 0 ] || fail "run as root"
[ -f "$INSTALLER" ] || fail "$INSTALLER not found; run deploy/build.sh first"
URL="http://127.0.0.1:$(setting DINNER_PORT)"

check_service() {
    step "Service"
    expect "/healthz" "$(http "$URL/healthz")" 200
    expect "/healthz body" "$(cat "$WORK/body")" ok
    rc-service dinner-log status >/dev/null || fail "rc-service dinner-log status"
    uid=$(id -u dinnerlog)
    n=0
    for d in /proc/[0-9]*; do
        args=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
        case "$args" in *bin/gunicorn*) ;; *) continue ;; esac
        n=$((n + 1))
        expect "uid of pid ${d#/proc/} ($args)" "$(awk '/^Uid:/ {print $2}' "$d/status")" "$uid"
    done
    [ "$n" -ge 2 ] || fail "expected a gunicorn master and a worker, found $n process(es)"
    expect "pidfile names the master" "$(tr '\0' ' ' < "/proc/$(cat "$PIDFILE")/cmdline" | grep -c gunicorn)" 1
}

check_owners_and_modes() {
    step "Owners and modes"
    expect "$APP_DIR" "$(stat -c '%U:%G' "$APP_DIR")" root:root
    expect "files in $APP_DIR not owned by root" "$(find "$APP_DIR" ! -user root | head -n 5)" ""
    expect "group/other-writable files in $APP_DIR" \
        "$(find "$APP_DIR" ! -type l \( -perm -020 -o -perm -002 \) | head -n 5)" ""
    expect "$DATA_DIR" "$(stat -c '%U:%G %a' "$DATA_DIR")" "dinnerlog:dinnerlog 750"
    expect "$CONF" "$(stat -c '%U:%G %a' "$CONF")" "root:root 600"
    expect "$BACKUP_DIR" "$(stat -c '%U:%G %a' "$BACKUP_DIR")" "dinnerlog:dinnerlog 700"
    expect "$BACKUP_JOB" "$(stat -c '%U %a' "$BACKUP_JOB")" "root 755"
    TOKEN=$(setting HISTORY_TOKEN)
    printf '%s' "$TOKEN" | grep -Eq '^[0-9a-f]{64}$' || fail "HISTORY_TOKEN isn't 64 hex characters"
    ok "HISTORY_TOKEN: 64 hex characters (not printed)"
}

check_dinner_with_photo() {
    step "A dinner with a JPEG"
    py 'import sys; from PIL import Image; Image.new("RGB", (2400, 1800), (200, 60, 40)).save(sys.argv[1], "JPEG")' \
        "$WORK/dinner.jpg"
    expect "POST /new" "$(http -F 'name=CI roast chicken' -F source_type=cookbook -F 'source_title=Example Cookbook' \
        -F source_detail=12 -F rating=8 -F "photos=@$WORK/dinner.jpg;type=image/jpeg" "$URL/new")" 302
    case "$(header location)" in
        */meal/*skipped=*) fail "the photo was skipped: $(header location)" ;;
        */meal/*) ok "redirected to $(header location)" ;;
        *) fail "unexpected redirect: $(header location)" ;;
    esac
    PHOTO=$(find "$DATA_DIR/photos" -type f -name '*.jpg' ! -name '*-t.jpg' -exec basename {} \;)
    expect "photos stored" "$(printf '%s\n' "$PHOTO" | grep -c .)" 1
    thumb="${PHOTO%.jpg}-t.jpg"
    [ -f "$DATA_DIR/photos/$thumb" ] || fail "no thumbnail $thumb beside $PHOTO"
    expect "photo owners" "$(stat -c %U "$DATA_DIR/photos/$PHOTO" "$DATA_DIR/photos/$thumb" | sort -u)" dinnerlog
    expect "long edges (photo, thumbnail)" "$(py 'import sys; from PIL import Image
print(*(max(Image.open(p).size) for p in sys.argv[1:]))' "$DATA_DIR/photos/$PHOTO" "$DATA_DIR/photos/$thumb")" "1600 480"
    expect "GET /photos/$PHOTO" "$(http "$URL/photos/$PHOTO")" 200
    expect "photo Content-Type" "$(header content-type)" image/jpeg
    cc=$(header cache-control)
    case "$cc" in
        *public*) fail "photo served with Cache-Control: $cc" ;;
        *private*) ok "photo Cache-Control: $cc" ;;
        *) fail "photo Cache-Control isn't private: '$cc'" ;;
    esac
}

check_history() {
    step "/api/history"
    expect "without a token" "$(http "$URL/api/history")" 401
    expect "with a wrong token" "$(http -H 'Authorization: Bearer not-the-token' "$URL/api/history")" 401
    expect "with HISTORY_TOKEN" "$(http -H "Authorization: Bearer $TOKEN" "$URL/api/history")" 200
    grep -q '"name":"CI roast chicken"' "$WORK/body" || fail "the dinner isn't in /api/history"
    ok "the dinner is in the history"
    ETAG=$(header etag)
    [ -n "$ETAG" ] || fail "no ETag on /api/history"
    expect "If-None-Match: <its ETag>" \
        "$(http -H "Authorization: Bearer $TOKEN" -H "If-None-Match: $ETAG" "$URL/api/history")" 304
}

check_backup() {
    step "Nightly backup job"
    rm -rf "${BACKUP_DIR:?}"/*
    "$BACKUP_JOB" || fail "$BACKUP_JOB exited $?"
    snap=$(find "$BACKUP_DIR" -name meals.db.gz)
    [ -n "$snap" ] || fail "no snapshot under $BACKUP_DIR; log: $(tail -n 3 /var/log/dinner-log-backup.log)"
    expect "snapshot owner" "$(stat -c %U "$snap")" dinnerlog
    gunzip -c "$snap" > "$WORK/snap.db"
    expect "snapshot integrity_check" \
        "$(py 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0])' \
        "$WORK/snap.db")" ok
    expect "the dinner is in the snapshot" \
        "$(py 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT count(*) FROM meals WHERE name=?", ("CI roast chicken",)).fetchone()[0])' \
        "$WORK/snap.db")" 1

    rm -rf "${BACKUP_DIR:?}"/*
    printf '#!/bin/sh\nexit 0\n' > "$FAKE_APP_BACKUP"
    chmod 755 "$FAKE_APP_BACKUP"
    "$BACKUP_JOB" || fail "$BACKUP_JOB exited $? with app-backup present"
    expect "snapshots with app-backup installed" "$(find "$BACKUP_DIR" -name meals.db.gz)" ""
    tail -n 1 /var/log/dinner-log-backup.log | grep -q "skipped: $FAKE_APP_BACKUP" \
        || fail "backup log doesn't say it skipped: $(tail -n 1 /var/log/dinner-log-backup.log)"
    ok "skipped while $FAKE_APP_BACKUP exists"
    rm -f "$FAKE_APP_BACKUP"
}

check_backfill_thumbs() {
    step "backfill_thumbs --dry-run, run as the README says (as root)"
    # shellcheck source=/dev/null
    out=$( (. "$CONF" && cd "$APP_DIR" && python3 -m backfill_thumbs --dry-run) ) || fail "exited $?: $out"
    printf '%s\n' "$out" | sed 's/^/   | /'
    printf '%s\n' "$out" | grep -q "$PHOTO.*already has a thumbnail" || fail "didn't report $PHOTO's thumbnail"
    expect "files under $DATA_DIR or $BACKUP_DIR not owned by dinnerlog" "$(not_dinnerlog)" ""
}

check_heic() {
    [ "${DINNER_HEIC:-}" = 1 ] || return 0
    step "HEIC (DINNER_HEIC=1)"
    # The installer carries on without HEIC when pillow-heif won't install, so check it really did.
    [ -d "$APP_DIR/vendor/pillow_heif" ] || fail "pillow-heif isn't in $APP_DIR/vendor"
    expect "PYTHONPATH in $CONF" "$(setting PYTHONPATH)" "$APP_DIR/vendor"
    PYTHONPATH="$APP_DIR/vendor" py 'import sys, pillow_heif; from PIL import Image
pillow_heif.from_pillow(Image.new("RGB", (1200, 900), (40, 120, 60))).save(sys.argv[1])' "$WORK/dinner.heic"
    before=$(find "$DATA_DIR/photos" -type f | wc -l)
    expect "POST /new with a HEIC" "$(http -F 'name=CI HEIC dinner' -F source_type=original \
        -F "photos=@$WORK/dinner.heic;type=image/heic" "$URL/new")" 302
    case "$(header location)" in *skipped=*) fail "the HEIC photo was skipped" ;; esac
    expect "photo files after the HEIC (photo + thumbnail)" "$(find "$DATA_DIR/photos" -type f | wc -l)" $((before + 2))
}

check_access_gate() {
    step "Cloudflare Access gate (placeholder team and AUD)"
    set_setting CF_TEAM_DOMAIN team.example.com
    set_setting CF_AUD aud-ci-placeholder
    restart
    expect "/ without a token" "$(http "$URL/")" 403
    expect "a photo without a token" "$(http "$URL/photos/$PHOTO")" 403
    expect "/healthz" "$(http "$URL/healthz")" 200
    expect "/api/history without its token" "$(http "$URL/api/history")" 401
    expect "/api/history by bearer" "$(http -H "Authorization: Bearer $TOKEN" "$URL/api/history")" 200

    set_setting LAN_ACCESS 1
    restart
    expect "/ from loopback, LAN_ACCESS=1" "$(http "$URL/")" 200
    expect "a photo from loopback, LAN_ACCESS=1" "$(http "$URL/photos/$PHOTO")" 200
    expect "/ from loopback marked as via Cloudflare (Cf-Ray)" "$(http -H 'Cf-Ray: 0000000000000000-CI' "$URL/")" 403
    expect "/ from loopback with a foreign Host (DNS rebinding)" "$(http -H 'Host: attacker.example' "$URL/")" 403

    # The refusals above are logged, so the log works; a signing-key fetch would have logged a warning too.
    grep -q "Access refused" "$LOG" || fail "no 'Access refused' line in $LOG; the checks below would prove nothing"
    if grep -q "signing keys" "$LOG"; then fail "the app tried to fetch Cloudflare's signing keys"; fi
    ok "no attempt to reach Cloudflare"
}

check_rerun() {
    step "Rerun the installer (gate on, LAN_ACCESS=1)"
    cp "$CONF" "$WORK/conf.before"
    (cd "$DATA_DIR/photos" && sha256sum -- *) > "$WORK/photos.before"
    pid=$(cat "$PIDFILE")
    rm -rf "${BACKUP_DIR:?}"/*
    sh "$INSTALLER" > "$WORK/rerun.log" 2>&1 || { cat "$WORK/rerun.log" >&2; fail "installer rerun exited $?"; }
    if ! cmp -s "$CONF" "$WORK/conf.before"; then
        changed=$(diff "$WORK/conf.before" "$CONF" | sed -n 's/^[-+]\(export \)\{0,1\}\([A-Z_]*\)=.*/\2/p' | sort -u)
        fail "the rerun changed $CONF: $(echo "$changed" | tr '\n' ' ')"
    fi
    ok "$CONF unchanged (token, Access settings, LAN_ACCESS, time zone)"
    grep -q "HISTORY_TOKEN: kept" "$WORK/rerun.log" || fail "installer didn't report keeping HISTORY_TOKEN"
    [ "$(cat "$PIDFILE")" != "$pid" ] || fail "the service wasn't restarted (same pid $pid)"
    ok "service restarted"
    wait_healthy
    expect "photos unchanged" "$(cd "$DATA_DIR/photos" && sha256sum -- * | cmp -s - "$WORK/photos.before" && echo same)" same
    expect "/api/history, same ETag as before" \
        "$(http -H "Authorization: Bearer $TOKEN" -H "If-None-Match: $ETAG" "$URL/api/history")" 304
    expect "/ from loopback, still LAN_ACCESS=1" "$(http "$URL/")" 200
    [ -n "$(find "$BACKUP_DIR" -name meals.db.gz)" ] || fail "no pre-upgrade snapshot in $BACKUP_DIR"
    ok "pre-upgrade snapshot taken"
    expect "files under $DATA_DIR or $BACKUP_DIR not owned by dinnerlog" "$(not_dinnerlog)" ""
}

check_service
check_owners_and_modes
check_dinner_with_photo
check_history
check_backup
check_backfill_thumbs
check_heic
check_access_gate
check_rerun
printf '\nAll installed-service checks passed.\n'
