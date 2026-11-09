# Dinner Log

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
| `COOKBOOK_URL` | (unset)    | Base URL of a Cookbook Collection instance, e.g. `https://cookbooks.example.com`; enables catalog autocomplete and links. A bare host gets `https://` added (`http://` for localhost and private addresses) |
| `COOKBOOK_API_URL` | `COOKBOOK_URL` | Address this server uses to call Cookbook Collection. Set it to the private one (e.g. `http://127.0.0.1:8767`) when both run on one host |
| `COOKBOOK_TOKEN` | (unset) | Cookbook Collection's `READ_TOKEN`; lets "Pick for tonight" draw on its recipes |
| `HISTORY_TOKEN` | (unset; the Alpine installer generates one) | Lets Cookbook Collection read cooking history from `/api/history` (see *Linking the two apps*). Unset, the endpoint doesn't exist |
| `TRUSTED_ORIGINS` | (unset) | Extra comma-separated origins allowed to POST (see Security). Rarely needed. |

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

It unpacks the app to a temporary folder and runs the same `deploy/install-alpine.sh`. Re-run the installer (either way) after `git pull` to upgrade. Your data is kept, and so are your settings in `/etc/conf.d/dinner-log`; any `DINNER_*` variable you pass updates just that setting. `DINNER_BIND` defaults to `0.0.0.0` (all interfaces); see Security below.

## Photos

Uploads are decoded only as JPEG, PNG or WebP (and HEIC when the optional `pillow-heif` package is installed). Pillow's other decoders are never used on uploads, and anything else, or an image over about 40 megapixels once decoded, is skipped and reported on the dinner's page. Each accepted photo is re-encoded as a JPEG with no EXIF, so phone location data is dropped, and saved as `<id>.jpg` (1600 px long edge) in `photos/`, with a 480 px thumbnail `<id>-t.jpg` beside it. The feed and the edit form show the thumbnail; a dinner's own page shows the full image. If a thumbnail is missing, the full image is shown instead. Photos are served with `Cache-Control: private, max-age=31536000, immutable` (their names never change), so a browser keeps them for a year but a shared cache or CDN in front of the app won't.

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

## Recipes from Cookbook Collection

With `COOKBOOK_URL` (or `COOKBOOK_API_URL`) set, the form looks up the cookbook you type in Cookbook Collection's public catalog. If that book's index is there, a **Recipe from this book** field appears listing its recipes. Picking one fills in the page, and the dinner name if it's empty or came from an earlier pick; anything you typed yourself is left alone. The cookbook title is also changed to the catalog's spelling, which helps Cookbook Collection match the dinner to the recipe later.

The browser asks Dinner Log (`/cookbook/recipes?title=…`), and Dinner Log asks Cookbook Collection's public `/api/books` and `/api/books/<id>`, caching each book's recipes for two minutes. No token is needed, and if the catalog is down the field simply doesn't appear.

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
