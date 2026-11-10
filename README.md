# Dinner Log

[![CI](https://github.com/SDNick484/dinner-log/actions/workflows/ci.yml/badge.svg)](https://github.com/SDNick484/dinner-log/actions/workflows/ci.yml)
[![Seam contract](https://github.com/SDNick484/dinner-log/actions/workflows/contract.yml/badge.svg)](https://github.com/SDNick484/dinner-log/actions/workflows/contract.yml)

A small self-hosted web app for keeping track of what your household cooks (or eats out) for dinner.

For each dinner you can record:

- **Day** and **what you had**
- **Where it came from**: a cookbook (title + page), a website (site + link), a restaurant (name + location), or your own creation
- A **1–10 rating** (optional)
- **Notes** and any number of **photos**

It's built for phones: a big "+" button, camera/gallery photo picker, installable to the home screen as a PWA. The log is searchable and grouped by month. Cookbook and restaurant names autocomplete from what you've entered before.

Flask + SQLite, no external services. Photos are stored on disk and downscaled on upload (1600 px long edge, JPEG, with a 480 px thumbnail beside each; see [Photos](#photos)).

## Quick start (Docker)

```sh
git clone https://github.com/SDNick484/dinner-log.git
cd dinner-log
cp .env.example .env     # optional: set APP_TZ, COOKBOOK_URL
docker compose up -d --build
```

Open <http://localhost:8000> (or `http://<server-ip>:8000` from other devices on your network).

All data lives in `./data` (`meals.db` and `photos/`). See *Backups* below. The image is Alpine-based and runs the app as an unprivileged `dinnerlog` user; on first start it takes ownership of `./data` (older images ran as root). The user is uid/gid 10001; if your storage refuses `chown` (some NFS/SMB mounts), make `./data` writable by that uid yourself. The `env_file` syntax in `docker-compose.yml` needs Docker Compose 2.24 or newer.

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
| `COOKBOOK_URL` | (unset)    | Base URL of a Cookbook Collection instance, e.g. `https://cookbooks.example.com`; enables catalog autocomplete and links. A bare host gets `https://` added (`http://` for localhost and private addresses) |
| `COOKBOOK_API_URL` | `COOKBOOK_URL` | Address this server uses to call Cookbook Collection. Set it to the private one (e.g. `http://127.0.0.1:8767`) when both run on one host |
| `COOKBOOK_TOKEN` | (unset) | Cookbook Collection's `READ_TOKEN`; lets "Pick for tonight" draw on its recipes |
| `HISTORY_TOKEN` | (unset; the Alpine installer generates one) | Lets Cookbook Collection read cooking history from `/api/history` (see *Linking the two apps*). Unset, the endpoint doesn't exist |
| `TRUSTED_ORIGINS` | (unset) | Extra comma-separated origins allowed to POST (see Security). Rarely needed. |
| `BACKUP_DIR` | `DATA_DIR/backups` (Alpine installer: `/var/backups/dinner-log`) | Where `backup.py` writes nightly database snapshots (see Backups) |
| `BACKUP_KEEP_DAYS` | `14` | Days of snapshots to keep |
| `BACKUP_TARGET` | (unset) | Folder on other storage, e.g. a NAS mount. Snapshots and photos are copied there too |
| `CF_TEAM_DOMAIN` | (unset) | Cloudflare Access team domain, e.g. `yourteam.cloudflareaccess.com`. With `CF_AUD`, every request must carry a valid Access token (see *Verifying Cloudflare Access*). Set both or neither |
| `CF_AUD` | (unset) | AUD tag of the Access application in front of Dinner Log |
| `LAN_ACCESS` | `0` | `1` = with Access verification on, also let in direct requests from the local network |
| `LAN_CIDRS` | loopback + private ranges | Comma-separated networks `LAN_ACCESS` allows, e.g. `192.168.1.0/24` |
| `LAN_HOSTS` | (unset) | Host names `LAN_ACCESS` accepts besides IP addresses and `localhost`, e.g. `dinners.lan` |

Use a single gunicorn worker; SQLite is happiest that way and one worker is plenty for a household.

## Alpine Linux / LXC

`deploy/install-alpine.sh` installs the app from a checkout as an OpenRC service (no Docker), running as an unprivileged user:

```sh
# from the repository root, as root
DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh deploy/install-alpine.sh
```

To install on a server without a checkout, build a one-file installer and copy that over instead:

```sh
sh deploy/build.sh                         # -> dist/setup-dinner-log.sh (app + installer in one file)
scp dist/setup-dinner-log.sh root@<server>:
DINNER_PORT=8000 DINNER_BIND=127.0.0.1 DINNER_TZ=America/Chicago sh setup-dinner-log.sh   # on the server, as root
```

Building needs `python3`, `base64` and `tar` (GNU or busybox) on the machine you build on. The installer unpacks the app to a temporary folder and runs the same `deploy/install-alpine.sh`. Re-run the installer (either way) after `git pull` to upgrade. Your data is kept, and so are your settings in `/etc/conf.d/dinner-log`; any `DINNER_*` variable you pass updates just that setting. `DINNER_BIND` defaults to `0.0.0.0` (all interfaces); see Security below.

## Photos

Uploads are decoded only as JPEG, PNG or WebP (and HEIC when the optional `pillow-heif` package is installed). Pillow's other decoders are never used on uploads, and anything else, or an image over about 40 megapixels once decoded, is skipped and reported on the dinner's page. Each accepted photo is re-encoded as a JPEG with no EXIF, so phone location data is dropped, and saved as `<id>.jpg` (1600 px long edge) in `photos/`, with a 480 px thumbnail `<id>-t.jpg` beside it. The feed and the edit form show the thumbnail; a dinner's own page shows the full image. If a thumbnail is missing, the full image is shown instead. Backups copy the thumbnails along with the photos; they can always be regenerated with `backfill_thumbs`. Photos are served with `Cache-Control: private, max-age=31536000, immutable` (their names never change), so a browser keeps them for a year but a shared cache or CDN in front of the app won't.

### HEIC photos (optional)

Phones that save HEIC (some iPhone and Samsung settings) need the optional [`pillow-heif`](https://pypi.org/project/pillow-heif/) package. Without it HEIC uploads are skipped with a notice that lists the formats the server does accept, and Safari normally converts to JPEG on upload anyway. It bundles libheif and ships musl wheels (x86_64 and aarch64), so it installs on Alpine without a compiler. Because that copy of libheif isn't patched through your package manager, update `pillow-heif` when it releases fixes. HEIC is also capped by the 40 megapixel limit above, and HEIC can't be decoded at reduced size like JPEG, so a 48 MP HEIC is refused.

- **Docker:** included in the image. Build with `--build-arg HEIC=0` to leave it out; where no wheel exists for the platform the build continues without it.
- **Alpine installer:** `DINNER_HEIC=1 sh deploy/install-alpine.sh` (or the one-file installer) installs it into `/opt/dinner-log/vendor` without dependencies, checks that it can really decode, and sets `PYTHONPATH` in `/etc/conf.d/dinner-log`. It needs Alpine's `py3-pillow` to be 11.1 or newer; if the check fails, the installer says so and carries on without HEIC. Re-run with `DINNER_HEIC=1` to update it; without the variable an existing install is kept.
- **A checkout:** `pip install -r deploy/requirements-heic.txt`.

Alpine also packages it as `py3-pillow_heif`, but only in the `testing` repository, so the wheel is the better route.

### Thumbnails for older photos

Photos uploaded before thumbnails existed have none until you run `backfill_thumbs`. It writes the missing ones from the stored 1600 px image, prints one line per photo and a total, and leaves existing thumbnails alone, so running it again changes nothing. A photo whose file is missing or unreadable is reported, the rest are still done, and the exit status is 1. Dry run first:

```sh
# Alpine (the config file is root-only; exported variables carry DATA_DIR)
( . /etc/conf.d/dinner-log && cd /opt/dinner-log && python3 -m backfill_thumbs --dry-run )
( . /etc/conf.d/dinner-log && cd /opt/dinner-log && python3 -m backfill_thumbs )
chown -R dinnerlog:dinnerlog /var/lib/dinner-log/photos   # it ran as root; keep the files the app's

# Docker
docker compose exec -u dinnerlog dinner-log python -m backfill_thumbs --dry-run

# A checkout
DATA_DIR=./data python3 -m backfill_thumbs --dry-run
```

```
photo    12  3f9c0a7e2b1d4c58a6e07d93b4f1a2c8.jpg: would write a thumbnail  [would]
photo    13  7be41d0c9a3f4e6d8b25c1f0a97d3e64.jpg: already has a thumbnail  [has]
2 photos checked: would write 1 thumbnails, 1 already had one, 0 errors (dry run: nothing written)
```

## Backups

`backup.py` takes a consistent snapshot of the database with SQLite's online backup API (safe while the app is running), checks it with `PRAGMA integrity_check`, gzips it, and keeps `BACKUP_KEEP_DAYS` days of them in `BACKUP_DIR`. With `BACKUP_TARGET` set it also copies each snapshot and any new photos there:

```
BACKUP_DIR/2026-11-09/meals.db.gz
BACKUP_TARGET/db/2026-11-09/dinner-log-meals.db.gz
BACKUP_TARGET/files/dinner-log/photos/<name>.jpg
```

- **Photos are only backed up to `BACKUP_TARGET`.** Without it, and with the local snapshots on the same disk as the data, a lost disk loses everything. Point it at other storage.
- Photos are copied once and never removed from the target, so deleting a dinner (or a photo) in the app doesn't delete the copy. Clean the target by hand if you need something gone.
- Snapshot folders are `0700` and files `0600`, owned by the service user: they hold every dinner and note. The target's permissions are up to you; it must be writable by the service user (uid 10001 under Docker).
- Exit status is non-zero when anything failed, including a `BACKUP_TARGET` that isn't a writable folder (an NFS share that didn't mount, say). The local snapshot is still taken.

**Alpine installer.** It installs `/etc/periodic/daily/dinner-log-backup` and enables `crond`; the job runs nightly as the `dinnerlog` user and logs to `/var/log/dinner-log-backup.log`. On upgrade it also takes a snapshot before restarting the service. If Cookbook Collection's installer has put its `app-backup` job on the same host, that job already backs up `/var/lib/dinner-log` (same layout on the target), so Dinner Log's job skips itself each night. Either install order works, and if `app-backup` is removed Dinner Log's job takes over. To run it by hand:

```sh
/etc/periodic/daily/dinner-log-backup; tail /var/log/dinner-log-backup.log
```

**Docker.** The container has no scheduler; add a line to the host's crontab:

```sh
# crontab -e on the Docker host
30 3 * * * cd /path/to/dinner-log && docker compose exec -T -u dinnerlog dinner-log python backup.py >> data/backup.log 2>&1
```

Snapshots go to `./data/backups`. For `BACKUP_TARGET`, mount the storage into the container (e.g. `- /mnt/nas/dinner-log:/backup-target` under `volumes:`) and set `BACKUP_TARGET=/backup-target` in `.env`.

### Restoring

Stop the app first so nothing writes while you swap the file.

```sh
# Alpine
rc-service dinner-log stop
gunzip -c /var/backups/dinner-log/2026-11-09/meals.db.gz > /var/lib/dinner-log/meals.db
rm -f /var/lib/dinner-log/meals.db-journal                    # a leftover journal would be replayed onto it
cp -a <BACKUP_TARGET>/files/dinner-log/photos/. /var/lib/dinner-log/photos/
chown -R dinnerlog:dinnerlog /var/lib/dinner-log
rc-service dinner-log start

# Docker (from the folder with docker-compose.yml)
docker compose stop
gunzip -c data/backups/2026-11-09/meals.db.gz > data/meals.db
rm -f data/meals.db-journal
cp -a <BACKUP_TARGET>/files/dinner-log/photos/. data/photos/
sudo chown -R 10001:10001 data       # the entrypoint only re-owns ./data when the folder itself isn't 10001's
docker compose start
```

Restoring from `BACKUP_TARGET` works the same way with `<BACKUP_TARGET>/db/<day>/dinner-log-meals.db.gz`. Photos the restored database doesn't mention are harmless; they just take space.

## Recipes from Cookbook Collection

With `COOKBOOK_URL` (or `COOKBOOK_API_URL`) set, the form looks up the cookbook you type in Cookbook Collection's public catalog. If that book's index is there, a **Recipe from this book** field appears listing its recipes. Picking one fills in the page, and the dinner name if it's empty or came from an earlier pick; anything you typed yourself is left alone. The cookbook title is also changed to the catalog's spelling, which helps Cookbook Collection match the dinner to the recipe later.

The browser asks Dinner Log (`/cookbook/recipes?title=…`), and Dinner Log asks Cookbook Collection's public `/api/books` and `/api/books/<id>`, caching each book's recipes for two minutes. No token is needed, and if the catalog is down the field simply doesn't appear.

**Links back.** A dinner picked from the catalog keeps its stable uids, and with `COOKBOOK_URL` set its page, and the Pick for tonight card, link straight to the book and recipe in Cookbook Collection (`<COOKBOOK_URL>/#/uid/<book uid>/<recipe uid>`). Recipes saved from websites are editor-only there, so those links go through `<COOKBOOK_URL>/admin#/uid/…` and ask for the editor login. Dinners without uids (typed by hand, or logged before uids existed) fall back to searching the catalog by name.

## Pick for tonight

**🎲 Pick for tonight** suggests a dinner. **Options ▾** next to it sets:

- **Mode:** *Favorite* (rated 8+ and not made in the last 3 weeks), *Try new* (a recipe you've never cooked), or *Surprise me* (a coin flip between the two, then anything not made lately). Without a mode, the Favorites chip decides, as before.
- **With / Without:** comma-separated ingredients, e.g. `chicken, lemon` and `pork`. Every *with* ingredient must be used; any *without* one rules a dish out.

On its own, Dinner Log only knows what dinners are called, so it matches ingredients against dish names (and notes, for *with*): "leeks" finds "Leek tart", "pea" doesn't find "Peach pie". Connect a [Cookbook Collection](https://github.com/SDNick484/cookbook-collection) instance and the picker also draws on its indexed recipes:

- *Try new* comes only from there.
- Ingredients match its ingredient tags as well as titles, so "chicken" finds a stew whose name doesn't say chicken.
- Dishes it has matched to a recipe are left to it (it knows their tags), so nothing is offered twice. Restaurants, your own dishes and unindexed books still come from Dinner Log.
- Tags cover only some recipes, so *without* is best effort. The card says when an ingredient has no tag and was matched by name only.
- If it doesn't answer, the picker falls back to past dinners and says so.

Setup: the call goes to `/admin/api/pick`, which is private (it returns ratings and cooking history). Set `COOKBOOK_TOKEN` to Cookbook Collection's `READ_TOKEN` (generated by its installer) and `COOKBOOK_API_URL` to its **private** address, e.g. `http://127.0.0.1:8767` when both run on the same host. The public address won't work: the login in front of `/admin` turns the request away, and Dinner Log refuses to follow that redirect so the token never goes to another host.

```sh
# /etc/conf.d/dinner-log (Alpine) or .env (Docker)
export COOKBOOK_URL=https://cookbooks.example.com     # links in the browser
export COOKBOOK_API_URL=http://127.0.0.1:8767         # server-to-server
export COOKBOOK_TOKEN=<READ_TOKEN from /etc/conf.d/cookbooks>
```

Under Docker, `127.0.0.1` is the container itself; use the host's address (or `host.docker.internal` with `extra_hosts: ["host.docker.internal:host-gateway"]`) and keep that port off the public network.

## Last time

When you're about to cook a dish again, Dinner Log shows what you said the previous times: the date, the rating and the start of the notes (about 140 characters) of up to three earlier dinners, newest first, each linking to that dinner. Above them is a line like *made 6 times · best 9/10*. It appears on a dinner's own page, on the Pick for tonight card, and on the dinner form once the name or the recipe you picked is settled. A dish you've never made shows nothing.

What counts as the same dish:

- **Same catalog recipe.** Two dinners with the same recipe uid match whatever they were called.
- **Same name, when either dinner has no recipe uid** (typed by hand, or logged before uids existed). Case, spacing, accents and plurals are ignored: "Leeks tart" is "leek tart", but "Chicken tikka" is not "Chicken tikka masala".
- **Different recipe uids never match**, even under the same name: two cookbooks' "Chicken soup" are two recipes.

The source (cookbook, restaurant, your own) doesn't matter, so a restaurant's "Burger" and your own "Burger" match by name.

On a dinner's own page (and when editing it) that dinner is left out and the line reads "6 other times"; later dinners are listed too, each with its date. The form fetches its list from `GET /last-time`, a read-only JSON route. It returns notes, so it sits behind the same login as every page and is never token-only, and the service worker doesn't cache it. There is nothing to configure and no data change: older dinners work as they are.

## Linking the two apps: cooking history

Cookbook Collection shows how often you've cooked each recipe and what you rated it. It reads that from Dinner Log's `GET /api/history`, a read-only list of the dinners it can match to its catalog: ones from a cookbook or website, or with a link. Restaurant and home-recipe dinners without a link are never sent.

The endpoint exists only when `HISTORY_TOKEN` is set, and every request must send it as `Authorization: Bearer <token>`. Cookbook Collection's `DINNERLOG_TOKEN` must hold the same value.

- **Alpine installer:** if `/etc/conf.d/dinner-log` has no `HISTORY_TOKEN` (or an empty one), the installer generates one; a token you've set is never replaced. Copy it to Cookbook Collection: `grep HISTORY_TOKEN /etc/conf.d/dinner-log`.
- **Docker:** set it yourself in `.env`, e.g. from `openssl rand -hex 32`.

Point Cookbook Collection at Dinner Log's **private** address (e.g. `http://127.0.0.1:8000` on the same host). The token travels in plain HTTP there, so keep it on loopback or a private network, or use https. Through the public address, the login in front of the site turns the request away anyway.

Dinners logged from Cookbook Collection (**Log tonight** on a catalog pick, the 🍽 link on a recipe, or the form's **Recipe from this book** picker) remember which book and recipe they came from, so Cookbook Collection doesn't have to guess from the title and page:

| Column | Holds |
|---|---|
| `cookbook_book_uid`, `cookbook_recipe_uid` | the catalog's stable uids (32 hex characters). Cookbook Collection trusts these first |
| `cookbook_book_id`, `cookbook_recipe_id` | its integer ids. Kept for catalogs older than uids; an id can be reused after the catalog deletes a row, so it's only used when no uid was sent |

Typing over the name, cookbook or page afterwards clears all four. Older dinners have none and are matched by title and page as before. `/api/history` sends all four; the fields are additive, so it's still version 1.

Upgrading: deploy Dinner Log first (the Alpine installer fills in `HISTORY_TOKEN` if it's empty), then upgrade Cookbook Collection and give it the URL and token. Either order works: each side ignores fields the other doesn't send yet. New columns are added on start and existing dinners aren't changed.

### Backfilling uids on older dinners

Dinners logged before uids existed have only the integer ids. `backfill_uids` resolves them once through the catalog's public `GET /api/books/<id>` and stamps the uids, but only where the catalog still agrees with the dinner: the book's title must match the dinner's cookbook, and the recipe's page or name must match the dinner's. A mismatch (the id now belongs to another book or recipe) is reported and left alone. Run it after upgrading Cookbook Collection, dry run first:

```sh
# Alpine (the config file is root-only; exported variables carry DATA_DIR and COOKBOOK_API_URL)
( . /etc/conf.d/dinner-log && cd /opt/dinner-log && python3 -m backfill_uids --dry-run )
( . /etc/conf.d/dinner-log && cd /opt/dinner-log && python3 -m backfill_uids )

# Docker
docker compose exec -u dinnerlog dinner-log python -m backfill_uids --dry-run

# A checkout
DATA_DIR=./data COOKBOOK_API_URL=http://127.0.0.1:8767 python3 -m backfill_uids --dry-run
```

It prints one line per dinner it looked at and a total, e.g.

```
meal    12  2026-10-01  'Roast chicken': would set book_uid=4f1c…, recipe_uid=9a0e…  [ok]
meal    31  2026-10-04  'Braised leeks': would set book_uid=4f1c…  [conflict (recipe left NULL): recipe 3 is now 'Polenta' p. 210]
88 meals checked: would set 85 book uids and 79 recipe uids; 2 conflicts, 1 meals left unchanged (dry run: nothing written)
```

It never replaces a uid that's already set, so running it again is harmless. If the catalog doesn't answer, it writes nothing and exits 1. Website recipes aren't in the public catalog, so those dinners keep their ints; Cookbook Collection still matches them by link.

## Security: read this before exposing it

**Dinner Log has no login of its own.** Unless you turn on *Verifying Cloudflare Access* below, anyone who can reach the port can read, add, edit and delete entries. Run it on a trusted network, or put it behind something that handles login, for example:

- a VPN such as WireGuard or Tailscale,
- a reverse proxy with authentication (basic auth, OAuth proxy, etc.),
- an identity-aware proxy such as Cloudflare Access or similar.

If you publish it on the internet, make sure the access policy is in place *before* the hostname goes live.

**Don't leave a side door open.** A proxy only protects the traffic that goes through it. If the app listens on all interfaces, anyone on the same network can skip the proxy and talk to it directly. When the proxy or tunnel runs on the same machine, bind to loopback: `DINNER_BIND=127.0.0.1` with the Alpine installer, or `"127.0.0.1:8000:8000"` under `ports:` in `docker-compose.yml`. When it runs elsewhere, firewall the port so only the proxy can reach it.

### Verifying Cloudflare Access

If Cloudflare Access is your login, Dinner Log can check Access's signed token itself, so the side door stays shut even if the app is reachable around the tunnel (a LAN client, a port left open, an Access application that doesn't cover every path). Set both:

```sh
# /etc/conf.d/dinner-log (Alpine) or .env (Docker)
export CF_TEAM_DOMAIN=yourteam.cloudflareaccess.com   # your Zero Trust team domain
export CF_AUD=<Application Audience (AUD) tag>         # on the Access application's overview
```

Then restart. Every request, including the service worker, manifest and icons, must carry a valid `Cf-Access-Jwt-Assertion` (Access adds it to every request it lets through): signed by your team's current key, for this application's AUD, not expired. Anything else gets a short 403 page; the reason is in the log (`/var/log/dinner-log.log`), e.g. `Access refused: GET / from 192.168.1.20: no Access token (LAN_ACCESS is off)`.

- **Unset, nothing changes.** With only one of the two set, the app refuses to start rather than run unprotected; likewise if PyJWT is missing (the Alpine installer adds `py3-jwt py3-cryptography`; Docker gets it from `requirements.txt`).
- **Exceptions.** `/api/history` is called by Cookbook Collection with its bearer token instead, and checks that token itself. `/healthz` answers `ok` to anyone, for health checks; it says nothing else.
- **Signing keys** come from `https://<team domain>/cdn-cgi/access/certs` and are cached. A token naming an unknown key triggers at most one fetch a minute, so junk tokens can't make the app hammer Cloudflare; if a fetch fails, the keys it already has keep working.
- **Clock.** Up to a minute of clock difference with Cloudflare is tolerated. More than that and fresh sign-ins are refused as "issued in the future"; keep the server's time synced.

**Using it directly on the home network.** With `LAN_ACCESS=1`, a request that didn't come through Cloudflare is let in when:

- the connecting address is in `LAN_CIDRS` (default: loopback and private ranges). Only the socket's address counts; forwarded headers are ignored;
- the URL uses an IP address (`http://192.168.1.5:8000`) or a name listed in `LAN_HOSTS`. This blocks DNS rebinding, where a web page re-points its own name at your server;
- it carries no Access token and no Cloudflare headers. A token that fails, or a request that came through the tunnel without one, is refused, never treated as LAN. This matters when `cloudflared` runs on the same machine: its connections come from `127.0.0.1`.

`LAN_ACCESS` means "anyone who can reach the port from those networks", so keep it off on networks you don't trust. **Under Docker**, check the address the app sees before relying on it: rootless Docker and Podman (and connections to a published port from the host itself) can show every client as one internal address, which would let everyone in. Leave it off there, or bind the port to loopback.

**Cross-site requests.** Because login lives in the proxy's cookie, the app refuses POSTs that browsers mark as coming from another site, including sibling subdomains (`Sec-Fetch-Site: cross-site` or `same-site`). This check doesn't depend on the `Host` header, so it works behind proxies that rewrite it (nginx does by default). For browsers that don't send that header, it falls back to comparing `Origin` with `Host` / `X-Forwarded-Host`. To let a specific sibling site post, list its origin in `TRUSTED_ORIGINS` (e.g. `https://other.example.com`).

## Installing on a phone

Open the site in Chrome on Android and choose **Add to Home screen** (or **Install app**). Chrome only offers a full install over HTTPS; over plain HTTP you still get a home-screen shortcut. If you use an authenticating proxy, the web manifest is requested with credentials so that installing keeps working behind it.

## Development

```sh
pip install -r requirements.txt pytest
pytest
CI_STRICT=1 pytest      # what CI runs: a skipped test fails the run
```

Some tests skip when something they need is missing (PyJWT, `pillow-heif`, `sh`/`tar`/`base64`, a git checkout). With `CI_STRICT=1` each skip fails the run and is listed with its reason; `CI_ALLOW_SKIP` takes comma-separated globs of test ids a run may skip (e.g. `tests/test_photos.py::test_real_heic_*` without `pillow-heif`).

## Continuous integration

GitHub Actions, on every push, weekly, and on pull requests from forks:

- **`CI`** ([ci.yml](.github/workflows/ci.yml))
  - *lint*: `sh -n` and `shellcheck` on the shell scripts.
  - *pytest*: the strict suite on Python 3.12, 3.13 and 3.14, with and without `pillow-heif`.
  - *alpine*: builds the one-file installer and runs it in an Alpine container under real OpenRC, on a pinned Alpine release (3.24) and, without blocking, on `alpine:latest` (each with and without `DINNER_HEIC=1`). Then it runs the strict suite on Alpine's Python packages and checks the installed service ([check-installed.sh](.github/scripts/check-installed.sh)): health, owners and modes, the generated `HISTORY_TOKEN`, photo upload and thumbnails, `/api/history`, the backup job, `backfill_thumbs`, the Access gate with placeholder settings, and a rerun of the installer.
  - *docker*: builds the image, runs it on an empty folder and checks the health check, a photo upload, uid 10001 ownership and a non-root process.
- **`Seam contract`** ([contract.yml](.github/workflows/contract.yml)): runs this app beside Cookbook Collection's `main` on loopback and checks the contract in its `docs/seam.md` (recipe uids, history matching by uid, "Pick for tonight" with `COOKBOOK_TOKEN`, `backfill_uids`). It also runs nightly, since the catalog changes on its own. To run it locally against your own checkouts:

```sh
sh .github/scripts/start-seam.sh ../cookbook-collection <catalog-venv> <this-venv> > seam.env   # adds catalog.test to /etc/hosts
set -a; . ./seam.env; set +a
CI_STRICT=1 pytest tests/contract
```

Dependabot proposes weekly updates for the Actions, the Docker base image and the pip requirements.

## License

MIT, see [LICENSE](LICENSE).
