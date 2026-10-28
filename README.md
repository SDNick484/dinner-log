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
docker compose up -d --build
```

Open <http://localhost:8000> (or `http://<server-ip>:8000` from other devices on your network).

All data lives in `./data` (`meals.db` and `photos/`). Back that folder up.

## Run without Docker

```sh
pip install -r requirements.txt
python app.py            # development server on :8000
# or, for real use:
DATA_DIR=/var/lib/dinner-log gunicorn -b 0.0.0.0:8000 -w 1 --threads 4 app:app
```

Environment variables:

| Variable   | Default        | Meaning                              |
|------------|----------------|--------------------------------------|
| `DATA_DIR` | `./data`       | Where the database and photos live   |
| `PORT`     | `8000`         | Port for `python app.py` only        |
| `APP_TZ`   | `UTC`          | IANA time zone for "today" (default day, catch-up, suggestions), e.g. `America/Chicago`. Set it, or evening dinners default to tomorrow's date. |
| `COOKBOOK_URL` | (unset)    | Base URL of a Cookbook Collection instance; enables catalog autocomplete and links |

Use a single gunicorn worker; SQLite is happiest that way and one worker is plenty for a household.

## Alpine Linux / LXC

`deploy/install-alpine.sh` installs the app from a checkout as an OpenRC service (no Docker), running as an unprivileged user:

```sh
# from the repository root, as root
DINNER_PORT=8000 DINNER_TZ=America/Chicago sh deploy/install-alpine.sh
```

Re-run it after `git pull` to upgrade. Your data is kept.

## Security: read this before exposing it

**Dinner Log has no authentication.** Anyone who can reach the port can read, add, edit and delete entries. Run it on a trusted network, or put it behind something that handles login, for example:

- a VPN such as WireGuard or Tailscale,
- a reverse proxy with authentication (basic auth, OAuth proxy, etc.),
- an identity-aware proxy such as Cloudflare Access or similar.

If you publish it on the internet, make sure the access policy is in place *before* the hostname goes live.

## Installing on a phone

Open the site in Chrome on Android and choose **Add to Home screen** (or **Install app**). Chrome only offers a full install over HTTPS; over plain HTTP you still get a home-screen shortcut. If you use an authenticating proxy, the web manifest is requested with credentials so that installing keeps working behind it.

## Development

```sh
pip install -r requirements.txt pytest
pytest
```

## License

MIT, see [LICENSE](LICENSE).
