# Dinner Log

A small self-hosted web app for keeping track of what your household cooks (or eats out) for dinner.

For each dinner you can record:

- **Day** and **what you had**
- **Where it came from**: a cookbook (title + page), a website (site + link), a restaurant (name + location), or your own creation
- A **1–10 rating** (optional)
- **Notes** and any number of **photos**

It's built for phones: a big "+" button, camera/gallery photo picker, installable to the home screen as a PWA. The log is searchable and grouped by month. Cookbook and restaurant names autocomplete from what you've entered before.

Flask + SQLite, no external services. Photos are stored on disk and downscaled on upload (1600 px long edge, JPEG).

## Quick start (Docker)

```sh
git clone https://github.com/SDNick484/dinner-log.git
cd dinner-log
cp .env.example .env     # optional: set APP_TZ, COOKBOOK_URL
docker compose up -d --build
```

Open <http://localhost:8000> (or `http://<server-ip>:8000` from other devices on your network).

All data lives in `./data` (`meals.db` and `photos/`). Back that folder up. The image is Alpine-based and runs the app as an unprivileged `dinnerlog` user; on first start it takes ownership of `./data` (older images ran as root). The user is uid/gid 10001; if your storage refuses `chown` (some NFS/SMB mounts), make `./data` writable by that uid yourself. The `env_file` syntax in `docker-compose.yml` needs Docker Compose 2.24 or newer.

## Run without Docker

```sh
pip install -r requirements.txt
python app.py            # development server on :8000
# or, for real use:
DATA_DIR=/var/lib/dinner-log gunicorn -b 0.0.0.0:8000 -w 1 --threads 4 app:app
```

Environment variables (see `.env.example`):

| Variable   | Default        | Meaning                              |
|------------|----------------|--------------------------------------|
| `DATA_DIR` | `./data`       | Where the database and photos live   |
| `PORT`     | `8000`         | Port for `python app.py` only        |
| `APP_TZ`   | `UTC`          | IANA time zone for "today" (default day, catch-up, suggestions), e.g. `America/Chicago`. Set it, or evening dinners default to tomorrow's date. |
| `COOKBOOK_URL` | (unset)    | Base URL of a Cookbook Collection instance; enables catalog autocomplete and links |
| `TRUSTED_ORIGINS` | (unset) | Extra comma-separated origins allowed to POST (see Security). Rarely needed. |

Use a single gunicorn worker; SQLite is happiest that way and one worker is plenty for a household.

## Alpine Linux / LXC

`deploy/install-alpine.sh` installs the app from a checkout as an OpenRC service (no Docker), running as an unprivileged user:

```sh
# from the repository root, as root
DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh deploy/install-alpine.sh
```

Re-run it after `git pull` to upgrade. Your data is kept, and so are your settings in `/etc/conf.d/dinner-log`; any `DINNER_*` variable you pass updates just that setting. `DINNER_BIND` defaults to `0.0.0.0` (all interfaces); see Security below.

## Security: read this before exposing it

**Dinner Log has no authentication.** Anyone who can reach the port can read, add, edit and delete entries. Run it on a trusted network, or put it behind something that handles login, for example:

- a VPN such as WireGuard or Tailscale,
- a reverse proxy with authentication (basic auth, OAuth proxy, etc.),
- an identity-aware proxy such as Cloudflare Access or similar.

If you publish it on the internet, make sure the access policy is in place *before* the hostname goes live.

**Don't leave a side door open.** A proxy only protects the traffic that goes through it. If the app listens on all interfaces, anyone on the same network can skip the proxy and talk to it directly. When the proxy or tunnel runs on the same machine, bind to loopback: `DINNER_BIND=127.0.0.1` with the Alpine installer, or `"127.0.0.1:8000:8000"` under `ports:` in `docker-compose.yml`. When it runs elsewhere, firewall the port so only the proxy can reach it.

**Cross-site requests.** Because login lives in the proxy's cookie, the app refuses POSTs that browsers mark as coming from another site, including sibling subdomains (`Sec-Fetch-Site: cross-site` or `same-site`). This check doesn't depend on the `Host` header, so it works behind proxies that rewrite it (nginx does by default). For browsers that don't send that header, it falls back to comparing `Origin` with `Host` / `X-Forwarded-Host`. To let a specific sibling site post, list its origin in `TRUSTED_ORIGINS` (e.g. `https://other.example.com`).

## Installing on a phone

Open the site in Chrome on Android and choose **Add to Home screen** (or **Install app**). Chrome only offers a full install over HTTPS; over plain HTTP you still get a home-screen shortcut. If you use an authenticating proxy, the web manifest is requested with credentials so that installing keeps working behind it.

## Development

```sh
pip install -r requirements.txt pytest
pytest
```

## License

MIT, see [LICENSE](LICENSE).
