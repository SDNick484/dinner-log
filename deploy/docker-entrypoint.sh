#!/bin/sh
# Container entrypoint: make sure the data volume belongs to the app user,
# then run the command unprivileged. Older images ran as root, so an existing
# ./data folder may still be root-owned; this fixes it once, on first start.
set -eu
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$DATA_DIR/photos"
    if [ "$(stat -c %U "$DATA_DIR")" != dinnerlog ]; then
        # Some mounts (NFS root_squash, CIFS) refuse chown; carry on and let
        # the app report a real write failure if there is one.
        chown -R dinnerlog:dinnerlog "$DATA_DIR" \
            || echo "warning: could not chown $DATA_DIR; make it writable by uid 10001" >&2
    fi
    exec su-exec dinnerlog:dinnerlog "$@"
fi
exec "$@"
