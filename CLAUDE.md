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
| `app.py` | Everything, top to bottom: config (`DATA_DIR`, `APP_TZ` via `_load_tz`, `local_today`), `SCHEMA` and the inline upgrade of old databases (runs at import), Access gate (`CF_*`, `LAN_*`, `AccessKeys` key cache, `access_claims`, `lan_access`, `@bearer_route` / `@public_route`, `require_access`), `refuse_cross_site_writes` (CSRF guard), `db()`, photos (`IMAGE_FORMATS`, `open_image`, `load_photo`, `write_thumb`, `thumb_file`, `save_photos`, `remove_photo_files`), cookbook id/uid parsing and `parse_meal_params`, Cookbook Collection client (`COOKBOOK_*`, `cookbook_book`, `title_key`, caches, `/cookbook/recipes`), link helpers (`cookbook_search_url`, `cookbook_uid_url`, `catalog_url`), template filters, "Pick for tonight" (`local_dishes`, `fetch_cookbook_picks`, `catalog_dish`, `pick_for_tonight`), "Last time" (`_name_key`, `previous_times`, `last_time_panel`), the feed (`index`, keyset paging on `(day, id)`), meal CRUD (`/new`, `/meal/<id>`, `/edit`, `/delete`), `/last-time` (JSON for the form's panel), `/photos/<file>`, `/api/history`, `/healthz`, `/sw.js` |
| `backup.py` | `python3 backup.py [--skip-if PATH]`: SQLite snapshot + integrity check + retention, copy to `BACKUP_TARGET`. Stdlib only, doesn't import `app`. Shipped; `deploy/dinner-log-backup` is its nightly cron wrapper |
| `backfill_uids.py` | One-off `python3 -m backfill_uids [--dry-run]`: stamps catalog uids on meals logged with only the integer ids. Shipped in the installer |
| `backfill_thumbs.py` | One-off `python3 -m backfill_thumbs [--dry-run]`: writes the missing 480 px `<name>-t.jpg` thumbnails for photos from before they existed. Idempotent; exit 1 if any photo is missing or unreadable. Shipped in the installer |
| `templates/` | `denied.html` (the Access 403 page, standalone), `base.html`, `index.html` (feed, filters, pick card, catch-up pills), `detail.html`, `form.html` (create/edit, recipe picker, builds the "Last time" panel in JS), `_pick_notes.html`, `_last_time.html` (the panel for detail and the pick card; keep it in step with the JS in `form.html`) |
| `static/` | `style.css`, `sw.js` (network-first service worker, cache name `dinner-log-v2`; it skips `/last-time`), manifest and icons |
| `.github/` | `workflows/ci.yml` (lint, pytest 3.12-3.14 x HEIC, Alpine installer under real OpenRC on 3.24 + non-blocking `latest`, Docker image), `workflows/contract.yml` (the seam with cookbook-collection `main`, also nightly), `scripts/` (`openrc-container.sh`, `check-installed.sh`, `check-docker.sh`, `start-seam.sh`), `dependabot.yml`. Keep the Alpine pin in `ci.yml` at the release production runs |
| `deploy/` | `build.sh` -> `dist/setup-dinner-log.sh` (one-file Alpine/OpenRC installer), `install-alpine.sh`, `history-token.sh`, `dinner-log-backup` (cron job), `docker-entrypoint.sh`, `requirements-heic.txt` (the optional `pillow-heif` pin; Docker `HEIC` build arg, installer `DINNER_HEIC=1`) |
| `tests/` | pytest (`pytest.ini` pins the rootdir, so node ids read `tests/...`). `conftest.py` installs `strict_skips.py`: with `CI_STRICT=1` any skip fails the run except node-id globs in `CI_ALLOW_SKIP` (self-tests in `test_strict_skips.py`). `contract/` checks the seam over HTTP against running apps and skips without `CONTRACT_*` settings (`.github/scripts/start-seam.sh` sets them). `test_app.py` has `load_app(data_dir)`; the rest build on it: `test_history*.py` (history feed and token), `test_uids.py`, `test_backfill.py`, `test_recipe_picker.py`, `test_suggest.py` (picker), `test_links.py` (catalog links, same-day grouping), `test_last_time.py` (same-dish matching, panel, `/last-time`), `test_photos.py` (decoder allow-list, HEIC with and without the plugin), `test_thumbs.py`, `test_backfill_thumbs.py`, `test_photo_cache.py`, `test_security.py`, `test_access.py` (Access gate, key cache, LAN rules; skipped without PyJWT), `test_backup.py`, `test_paging.py`, `test_config.py`, `test_build.py` (the installer payload), `test_hygiene.py` (public-repo hygiene, same rules as Cookbook Collection's) |

## Commands
```sh
python -m pytest -q                                  # full suite, ~6 s
CI_STRICT=1 python -m pytest -q                      # as CI runs it: any skip fails the run
sh deploy/build.sh                                   # -> dist/setup-dinner-log.sh (OUT=<path> to move it); needs python3
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
- "Last time" (`previous_times`): two dinners are the same dish when both carry a recipe uid and the uids are equal, or when
  either lacks one and `_name_key` matches. Same name under different uids is two recipes. Keep it symmetric. `/last-time`
  returns notes: it stays behind the normal gate (never a `@bearer_route`), clips notes in `last_time_panel`, and `sw.js`
  must keep skipping it, since the worker ignores `no-store`. Render its notes in JS with `textContent` only.
- Photos: `load_photo` opens uploads only as `IMAGE_FORMATS` (JPEG, PNG, WebP, and HEIF when `pillow_heif` is installed;
  never widen it, Pillow's other decoders are an attack surface), checks the header pixel count before decoding and
  draft-decodes any `JpegImageFile` (that includes MPO). `save_photos` downscales to `MAX_PHOTO_EDGE`, re-encodes JPEG,
  drops EXIF (incl. GPS) and writes a `THUMB_EDGE` thumbnail beside it (`thumb_name`: `x.jpg` -> `x-t.jpg`, derived, no
  column). Templates use `thumb_file()` for the feed and the form, which falls back to the full image until a thumbnail
  exists. `/photos/<file>` is `private, ..., immutable`: never `public`. Originals never touch the repo or the data dir. Keep `data/` out of git.
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
  `tests/test_hygiene.py` fails on container/VM IDs and home-directory paths in tracked files; hostnames, names and
  emails can't be listed there without publishing them, so check your diff for those yourself.
- Conventional commits (`feat:`, `fix:`, `docs:`), small and reviewable.

## Release flow
Develop on a branch, run the tests, open a PR, merge it, then `sh deploy/build.sh` and hand over
`dist/setup-dinner-log.sh`. The installer is safe to rerun: it keeps the database, photos and `/etc/conf.d/dinner-log`.
