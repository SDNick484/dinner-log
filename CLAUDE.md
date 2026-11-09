# Dinner Log: notes for Claude

Flask + SQLite family dinner log, photos on disk. Mobile-first server-rendered pages (Jinja), no JS framework.
It has no login of its own: it runs behind a reverse proxy or tunnel that authenticates (Cloudflare Access, a VPN).
With `CF_TEAM_DOMAIN` + `CF_AUD` set it also verifies Cloudflare Access's token on every request (opt-in).
README.md is the user-facing guide; this file is how to work on the code. Its sibling is Cookbook Collection
(github.com/SDNick484/cookbook-collection): separate app, separate database. The contract between the two is
`docs/seam.md` in that repo; read it before touching ids, `/api/history`, or links into the catalog.

## Map
| File | What's in it |
|---|---|
| `app.py` | Everything, top to bottom: config (`DATA_DIR`, `APP_TZ` via `_load_tz`, `local_today`), `SCHEMA` and the inline upgrade of old databases (runs at import), Access gate (`CF_*`, `LAN_*`, `AccessKeys` key cache, `access_claims`, `lan_access`, `@bearer_route` / `@public_route`, `require_access`), `refuse_cross_site_writes` (CSRF guard), `db()`, photos (`save_photos`, `remove_photo_files`), cookbook id/uid parsing and `parse_meal_params`, Cookbook Collection client (`COOKBOOK_*`, `cookbook_book`, `title_key`, caches, `/cookbook/recipes`), link helpers (`cookbook_search_url`, `cookbook_uid_url`, `catalog_url`), template filters, "Pick for tonight" (`local_dishes`, `fetch_cookbook_picks`, `catalog_dish`, `pick_for_tonight`), the feed (`index`, keyset paging on `(day, id)`), meal CRUD (`/new`, `/meal/<id>`, `/edit`, `/delete`), `/photos/<file>`, `/api/history`, `/healthz`, `/sw.js` |
| `backup.py` | `python3 backup.py [--skip-if PATH]`: SQLite snapshot + integrity check + retention, copy to `BACKUP_TARGET`. Stdlib only, doesn't import `app`. Shipped; `deploy/dinner-log-backup` is its nightly cron wrapper |
| `backfill_uids.py` | One-off `python3 -m backfill_uids [--dry-run]`: stamps catalog uids on meals logged with only the integer ids. Shipped in the installer |
| `templates/` | `denied.html` (the Access 403 page, standalone), `base.html`, `index.html` (feed, filters, pick card, catch-up pills), `detail.html`, `form.html` (create/edit, recipe picker), `_pick_notes.html` |
| `static/` | `style.css`, `sw.js` (network-first service worker, cache name `dinner-log-v2`), manifest and icons |
| `deploy/` | `build.sh` -> `dist/setup-dinner-log.sh` (one-file Alpine/OpenRC installer), `install-alpine.sh`, `history-token.sh`, `dinner-log-backup` (cron job), `docker-entrypoint.sh` |
| `tests/` | pytest. `test_app.py` has `load_app(data_dir)`; the rest build on it: `test_history*.py` (history feed and token), `test_uids.py`, `test_backfill.py`, `test_recipe_picker.py`, `test_suggest.py` (picker), `test_links.py` (catalog links, same-day grouping), `test_photos.py`, `test_security.py`, `test_access.py` (Access gate, key cache, LAN rules; skipped without PyJWT), `test_backup.py`, `test_paging.py`, `test_config.py`, `test_build.py` (the installer payload) |

## Commands
```sh
python -m pytest -q                                  # full suite, ~6 s
sh deploy/build.sh                                   # -> dist/setup-dinner-log.sh (OUT=<path> to move it)
DATA_DIR=$(mktemp -d) python app.py                  # dev server on :8000
docker compose up --build                            # reads .env (copy .env.example)
```
Deps: `pip install -r requirements.txt pytest`. The Alpine installer's package list lives in `deploy/install-alpine.sh`.

## Conventions
- Single gunicorn worker (`-w 1 --threads 4`), so the process-local caches (`_cookbook_cache`, `_recipe_cache`) are fine here.
  Don't add a second worker without moving them.
- Schema changes are additive and happen inline at import: add the column to `SCHEMA` (fresh databases) and an
  `ALTER TABLE` guarded by `PRAGMA table_info` (existing ones). Existing rows get NULL; fill them with a script
  like `backfill_uids.py`. State the migration impact in the PR.
- Stable ids: store the catalog's `uid`s (32 lowercase hex, `parse_cookbook_uid`); the integer ids are legacy and can be
  reused after a catalog delete. Only meals with a catalog source (`CATALOG_SOURCES`) keep ids.
- `/api/history` is a contract with Cookbook Collection. New fields are additive and keep `HISTORY_VERSION = 1`; bump
  only for a breaking change, and upgrade Cookbook Collection first.
- Links into the catalog: use `catalog_url(item, recipe=...)` / `cookbook_uid_url`. They return `""` without a
  `COOKBOOK_URL` or a valid uid, so keep the name-search fallback (`cookbook_search_url`). Recipe websites
  (`source_type='online'`) are editor-only in the catalog, so those links go through `/admin`.
- Auth: with the Access gate off (the default) the proxy in front is the only gate; with it on, `require_access` checks
  every request, static files and photos included. Every POST also goes through `refuse_cross_site_writes`; don't
  loosen either. A route another server calls with a bearer token gets `@bearer_route` and checks its token itself;
  `@public_route` is for `/healthz` only. Never exempt by path; `test_access.py::test_exemptions_are_explicit` pins the
  list. The LAN rule trusts only the socket peer: don't add ProxyFix or trust X-Forwarded-For. Say what a new route
  exposes in its PR.
- Photos: `save_photos` downscales to `MAX_PHOTO_EDGE`, re-encodes JPEG and drops EXIF (incl. GPS); header pixel check
  before decoding. Originals never touch the repo or the data dir. Keep `data/` out of git.
- A new runtime file (module, script) goes in three places: the `cp` line in `deploy/build.sh`, the `cp` line in
  `deploy/install-alpine.sh`, and a `COPY` in the `Dockerfile`; `test_build.py::test_payload_is_the_app_only` pins the
  installer's contents. `CLAUDE.md`, `README.md`, `tests/` are dev-only and are not shipped.
- A new env var goes in `.env.example`, the README table, and `set_conf` in `deploy/install-alpine.sh` (as `export NAME`,
  so the OpenRC service sees it). Compose passes `.env` through; only add a Dockerfile `ENV` for a default. Settings are
  read at import time: tests set them with `monkeypatch.setenv` *before* `load_app`, or patch the module attribute after
  (`monkeypatch.setattr(a, "COOKBOOK_URL", ...)`).
- Tests: `load_app(tmp_path)` re-imports `app` bound to a fresh `DATA_DIR`. Never make real network calls: patch
  `fetch_cookbook_picks`, `urllib.request.urlopen` or the `cookbook_*` helpers. Test data-model and parsing logic
  properly; don't over-test the UI.
- Keep READMEs accurate: update the README when setup steps, env vars or behaviour change.
- Public repo (MIT): no real hostnames, IPs, container/VM IDs, tunnel IDs, tokens, family names or personal paths in
  code, docs, examples or commit messages. Use `cookbooks.example.com` / `dinner.example.com` and loopback addresses.
  There is no hygiene test here (unlike Cookbook Collection), so grep your own diff for them.
- Conventional commits (`feat:`, `fix:`, `docs:`), small and reviewable.

## Release flow
Develop on a branch, run the tests, open a PR, merge it, then `sh deploy/build.sh` and hand over
`dist/setup-dinner-log.sh`. The installer is safe to rerun: it keeps the database, photos and `/etc/conf.d/dinner-log`.
