#!/bin/sh
# history-token.sh CONF: make sure HISTORY_TOKEN has a value in Dinner Log's config.
# A token already set is kept as it is. A missing or empty one gets a new random 64-hex-digit token,
# written in place. Prints "kept" or "generated", never the token itself.
set -eu
CONF=$1
COMMENT="# Token Cookbook Collection uses to read cooking history (optional)"

current=$(sed -n 's/^\(export \)\{0,1\}HISTORY_TOKEN=//p' "$CONF" | tail -n 1 | tr -d "\"' \t")
if [ -n "$current" ]; then
    echo "HISTORY_TOKEN: kept the one in $CONF"
    exit 0
fi

token=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
[ ${#token} -eq 64 ] || { echo "Couldn't read /dev/urandom for HISTORY_TOKEN" >&2; exit 1; }
if grep -q '^\(export \)\{0,1\}HISTORY_TOKEN=' "$CONF"; then
    sed -i "s|^\(export \)\{0,1\}HISTORY_TOKEN=.*|export HISTORY_TOKEN=$token|" "$CONF"
else
    printf '%s\nexport HISTORY_TOKEN=%s\n' "$COMMENT" "$token" >> "$CONF"
fi
echo "HISTORY_TOKEN: generated a new one in $CONF"
