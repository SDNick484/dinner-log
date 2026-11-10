FROM python:3.12-alpine
WORKDIR /app
# su-exec: drop from root to the app user after fixing data-dir ownership.
# Fixed uid/gid 10001 so ./data's owner on the host is stable across rebuilds.
RUN apk add --no-cache su-exec \
 && addgroup -S -g 10001 dinnerlog \
 && adduser -S -D -H -u 10001 -G dinnerlog dinnerlog
COPY requirements.txt ./
COPY deploy/requirements-heic.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Optional HEIC support (iPhone photos). pillow-heif bundles libheif and ships musl wheels for x86_64 and
# aarch64; where there is no wheel the build carries on without it. Skip it with --build-arg HEIC=0.
ARG HEIC=1
RUN if [ "$HEIC" = 1 ]; then \
      pip install --no-cache-dir --only-binary=:all: -r requirements-heic.txt \
      || echo "pillow-heif has no wheel for this platform; HEIC photos stay unsupported"; \
    fi
COPY app.py backfill_uids.py backfill_thumbs.py backup.py ./
COPY templates ./templates
COPY static ./static
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENV DATA_DIR=/data \
    APP_TZ=UTC \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD wget -q -O /dev/null http://127.0.0.1:8000/healthz || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
# Single worker is plenty for a family; SQLite likes it that way. No control socket: gunicorn would put it
# under the app user's $HOME, which the image doesn't have, and nothing here uses gunicornc.
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "--threads", "4", "--no-control-socket", "app:app"]
