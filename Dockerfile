FROM python:3.12-alpine
WORKDIR /app
# su-exec: drop from root to the app user after fixing data-dir ownership.
# Fixed uid/gid 10001 so ./data's owner on the host is stable across rebuilds.
RUN apk add --no-cache su-exec \
 && addgroup -S -g 10001 dinnerlog \
 && adduser -S -D -H -u 10001 -G dinnerlog dinnerlog
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY templates ./templates
COPY static ./static
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENV DATA_DIR=/data \
    APP_TZ=UTC \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD wget -q -O /dev/null http://127.0.0.1:8000/ || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
# Single worker is plenty for a family; SQLite likes it that way.
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "--threads", "4", "app:app"]
