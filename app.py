"""Dinner Log: a tiny self-hosted app for tracking family dinners.

Flask + SQLite, photos stored on disk. No auth: run it behind your LAN/VPN.
"""
import datetime
import ipaddress
import json
import os
import random
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Flask, abort, g, redirect, render_template, request,
                   send_from_directory, url_for)
from PIL import Image, ImageOps

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
DB_PATH = os.path.join(DATA_DIR, "meals.db")
os.makedirs(PHOTO_DIR, exist_ok=True)

SOURCE_TYPES = {"cookbook": "Cookbook", "online": "Online",
                "restaurant": "Restaurant", "original": "Our own"}
def _load_tz(name):
    """IANA zone from APP_TZ (e.g. America/Chicago). Empty or unknown -> UTC,
    with a warning, rather than refusing to start."""
    name = (name or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        import logging
        logging.getLogger(__name__).warning("Unknown APP_TZ %r; using UTC", name)
        return ZoneInfo("UTC")


APP_TZ = _load_tz(os.environ.get("APP_TZ"))
MAX_PHOTO_EDGE = 1600  # phone photos are huge; downscale to keep storage sane
MAX_DECODE_PIXELS = 40_000_000  # after JPEG draft scaling; ~120 MB as RGB

def local_today(now=None):
    """Today's date in the household's time zone, not the server's (usually UTC)."""
    now = now or datetime.datetime.now(APP_TZ)
    return now.astimezone(APP_TZ).date()


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,                       -- ISO date, YYYY-MM-DD
    name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'original',
    source_title TEXT NOT NULL DEFAULT '',   -- cookbook title, site name, or restaurant
    source_detail TEXT NOT NULL DEFAULT '',  -- page number, URL, or restaurant location
    notes TEXT NOT NULL DEFAULT '',
    rating INTEGER CHECK (rating BETWEEN 1 AND 10),  -- NULL = not rated
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cookbook_book_id INTEGER,                -- Cookbook Collection books.id, when picked from its catalog
    cookbook_recipe_id INTEGER               -- Cookbook Collection recipes.id
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    filename TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meals_day ON meals(day DESC);
CREATE INDEX IF NOT EXISTS idx_photos_meal_id ON photos(meal_id);
"""


TRUSTED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("TRUSTED_ORIGINS", "").split(",")
                   if o.strip()}


@app.before_request
def refuse_cross_site_writes():
    """CSRF defense in depth: the app has no session of its own, but an
    authenticating proxy's cookie may ride along on a form post from another
    site. Browsers label such requests; refuse them.

    Fetch Metadata (Sec-Fetch-Site) is the primary signal because it doesn't
    depend on the Host header, which many reverse proxies rewrite (nginx's
    default sends the upstream address). Only when a browser doesn't send it
    do we compare Origin with the Host / X-Forwarded-Host. Requests with
    neither header (curl, scripts) pass: they can't carry a victim's cookie.
    TRUSTED_ORIGINS lets a named sibling site post anyway."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and origin in TRUSTED_ORIGINS:
        return None
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        if site not in ("same-origin", "none"):
            abort(403)  # cross-site, or same-site from a sibling subdomain
        return None
    if origin:
        netloc = urllib.parse.urlsplit(origin).netloc
        hosts = {request.host, request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()}
        if not netloc or netloc not in hosts:
            abort(403)  # includes "null", which a sandboxed attacker frame sends
    return None


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


with sqlite3.connect(DB_PATH) as _c:
    _c.executescript(SCHEMA)
    # Upgrade databases created before ratings existed.
    if "rating" not in [r[1] for r in _c.execute("PRAGMA table_info(meals)")]:
        _c.execute("ALTER TABLE meals ADD COLUMN rating INTEGER "
                   "CHECK (rating BETWEEN 1 AND 10)")
    _c.execute("CREATE INDEX IF NOT EXISTS idx_photos_meal_id ON photos(meal_id)")
    # Upgrade databases created before meals carried Cookbook Collection ids.
    _cols = [r[1] for r in _c.execute("PRAGMA table_info(meals)")]
    for _col in ("cookbook_book_id", "cookbook_recipe_id"):
        if _col not in _cols:
            _c.execute(f"ALTER TABLE meals ADD COLUMN {_col} INTEGER")


def save_photos(meal_id, files):
    """Downscale and store uploads. Returns how many files were skipped.

    Pixels are checked from the header before anything is decoded, and JPEGs
    are decoded at reduced scale (draft), so a huge or hostile image can't
    exhaust a small container's memory. EXIF (including GPS) is not carried
    over to the saved JPEG; orientation is applied to the pixels first.
    """
    skipped = 0
    for f in files:
        if not f or not f.filename:
            continue
        try:
            img = Image.open(f.stream)
            if img.format == "JPEG":
                img.draft("RGB", (MAX_PHOTO_EDGE, MAX_PHOTO_EDGE))
            if img.width * img.height > MAX_DECODE_PIXELS:
                raise ValueError("image too large to decode safely")
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception as exc:
            app.logger.info("Skipped photo %r: %s", f.filename, exc)
            skipped += 1
            continue
        img.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE))
        name = uuid.uuid4().hex + ".jpg"
        img.save(os.path.join(PHOTO_DIR, name), "JPEG", quality=85, optimize=True)
        db().execute("INSERT INTO photos (meal_id, filename) VALUES (?, ?)", (meal_id, name))
    return skipped


def remove_photo_files(names):
    for n in names:
        try:
            os.remove(os.path.join(PHOTO_DIR, n))
        except FileNotFoundError:
            pass


CATALOG_SOURCES = ("cookbook", "online")


def parse_cookbook_id(raw):
    """A Cookbook Collection id from a form or link: a positive integer, else None."""
    raw = str(raw if raw is not None else "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and int(raw) > 0 else None


def parse_meal_params(params, default_source="cookbook"):
    if "source_type" in params:
        stype = str(params.get("source_type") or "")
        if stype not in SOURCE_TYPES:
            stype = "original"
    else:
        stype = default_source
    day = str(params.get("day") or "").strip() or local_today().isoformat()
    try:
        day = datetime.date.fromisoformat(day).isoformat()  # normalize e.g. 20261028
    except ValueError:
        day = local_today().isoformat()
    raw = str(params.get("rating") or "").strip()
    rating = int(raw) if raw.isdigit() and 1 <= int(raw) <= 10 else None
    return {
        "day": day,
        "name": str(params.get("name") or "").strip(),
        "source_type": stype,
        "source_title": str(params.get("source_title") or "").strip(),
        "source_detail": str(params.get("source_detail") or "").strip(),
        "notes": str(params.get("notes") or "").strip(),
        "rating": rating,
        # Only meals from Cookbook Collection's catalog keep its ids.
        "cookbook_book_id": parse_cookbook_id(params.get("cookbook_book_id")) if stype in CATALOG_SOURCES else None,
        "cookbook_recipe_id": parse_cookbook_id(params.get("cookbook_recipe_id")) if stype in CATALOG_SOURCES else None,
    }



def form_values():
    v = parse_meal_params(request.form, default_source="original")
    if not v["name"]:
        v["name"] = "Untitled dinner"
    return v



def base_url(raw):
    """A configured address as an absolute base URL. Without a scheme ("cookbooks.example.com") a link
    would be relative to this site, so add one: http:// for localhost and private addresses, else https://."""
    raw = (raw or "").strip().rstrip("/")
    if not raw or "://" in raw:
        return raw
    host = urllib.parse.urlsplit("//" + raw).hostname or ""
    try:
        private = host == "localhost" or ipaddress.ip_address(host).is_private
    except ValueError:
        private = False
    return ("http://" if private else "https://") + raw


COOKBOOK_URL = base_url(os.environ.get("COOKBOOK_URL"))  # for links in the browser
# Where this server calls Cookbook Collection. Point it at the private address (e.g. http://127.0.0.1:8767)
# when both run on one host: faster, and /admin/api/pick sits behind the login on the public one.
COOKBOOK_API_URL = base_url(os.environ.get("COOKBOOK_API_URL")) or COOKBOOK_URL
COOKBOOK_TOKEN = os.environ.get("COOKBOOK_TOKEN", "").strip()  # Cookbook Collection's READ_TOKEN
_cookbook_cache = {"cached_at": 0, "titles": [], "ids": {}}
_recipe_cache = {}  # book id -> (fetched_at, [{"name", "page"}])
RECIPE_TTL = 120


def cookbook_search_url(query):
    q = str(query or "").strip()
    if not COOKBOOK_URL or not q:
        return ""
    return f"{COOKBOOK_URL}/#/?q={urllib.parse.quote_plus(q)}"


@app.template_filter("cookbook_search_url")
def filter_cookbook_search_url(query):
    return cookbook_search_url(query)


@app.context_processor
def inject_cookbook_config():
    return {
        "cookbook_url": COOKBOOK_URL,
        "cookbook_search_url": cookbook_search_url,
    }


def fetch_cookbook_titles(timeout=1.5):
    if not COOKBOOK_API_URL:
        return []
    now = time.time()
    if now - _cookbook_cache["cached_at"] < 60:
        return _cookbook_cache["titles"]

    req_url = f"{COOKBOOK_API_URL}/api/books"
    try:
        with urllib.request.urlopen(req_url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            books = data.get("books", [])
            titles = [b["title"].strip() for b in books if b.get("title")]
            _cookbook_cache["titles"] = titles
            _cookbook_cache["ids"] = {title_key(b["title"]): b["id"] for b in books
                                      if b.get("title") and isinstance(b.get("id"), int)}
            _cookbook_cache["cached_at"] = now
            return titles
    except Exception as exc:
        app.logger.warning("Failed to fetch books from Cookbook Collection (%s): %s", req_url, exc)
        _cookbook_cache["cached_at"] = now - 45
        return _cookbook_cache["titles"]


def title_key(title):
    """How a typed cookbook title is compared with the catalog's: no case, accents, spacing or
    leading article, so "the zuni cafe cookbook" finds "The Zuni Café Cookbook"."""
    plain = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", plain)
    if words and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)


def cookbook_recipes(title, timeout=2.0):
    """(catalog title, [{"name", "page"}]) for a cookbook in Cookbook Collection's index, or (None, [])
    when it isn't there, isn't indexed, or the catalog doesn't answer. Uses the public API only."""
    fetch_cookbook_titles()
    key = title_key(title)
    book_id = _cookbook_cache["ids"].get(key) if key else None
    if book_id is None:
        return None, []
    now = time.time()
    hit = _recipe_cache.get(book_id)
    if hit and now - hit[0] < RECIPE_TTL:
        return hit[1], hit[2]
    req_url = f"{COOKBOOK_API_URL}/api/books/{book_id}"
    try:
        with urllib.request.urlopen(req_url, timeout=timeout) as resp:
            book = json.loads(resp.read().decode("utf-8"))
        recipes = [{"name": r["name"].strip(), "page": (r.get("page") or "").strip()}
                   for r in book.get("recipes", []) if (r.get("name") or "").strip()]
        _recipe_cache[book_id] = (now, book.get("title") or title, recipes)
        return book.get("title") or title, recipes
    except Exception as exc:
        app.logger.warning("Failed to fetch recipes from Cookbook Collection (%s): %s", req_url, exc)
        return (hit[1], hit[2]) if hit else (None, [])


@app.route("/cookbook/recipes")
def cookbook_recipes_route():
    """The form's recipe picker: recipes indexed for the cookbook typed in "Cookbook".
    Same-origin, so the browser never talks to Cookbook Collection directly."""
    book, recipes = cookbook_recipes(request.args.get("title", "")[:200])
    return {"book": book, "recipes": recipes}


def titles_for(source_type):
    """Past cookbook / restaurant names, for autocomplete."""
    rows = db().execute(
        "SELECT DISTINCT source_title FROM meals "
        "WHERE source_type=? AND source_title != '' ORDER BY source_title COLLATE NOCASE",
        (source_type,))
    local_titles = [r["source_title"] for r in rows]
    if source_type != "cookbook":
        return local_titles

    remote_titles = fetch_cookbook_titles()
    seen = set()
    merged = []
    for t in local_titles + remote_titles:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append(t.strip())
    merged.sort(key=lambda s: s.lower())
    return merged


@app.template_filter("pretty_day")
def pretty_day(iso):
    d = datetime.date.fromisoformat(iso)
    return d.strftime("%a %b ") + str(d.day)


@app.template_filter("month_label")
def month_label(iso):
    return datetime.date.fromisoformat(iso).strftime("%B %Y")


def recent_missing_days(days=7, today=None):
    if days <= 0:
        return []
    if today is None:
        today = local_today()
    window = [(today - datetime.timedelta(days=i)).isoformat() for i in range(days)]

    start_day = window[-1]
    end_day = window[0]
    rows = db().execute(
        "SELECT DISTINCT day FROM meals WHERE day >= ? AND day <= ?",
        (start_day, end_day)
    ).fetchall()
    logged = {r["day"] for r in rows}
    return [d for d in window if d not in logged]


@app.template_filter("gap_label")
def gap_label(iso, today=None):
    if today is None:
        today = local_today()
    d = datetime.date.fromisoformat(iso)
    if d == today:
        return "Today"
    if d == today - datetime.timedelta(days=1):
        return "Yesterday"
    return d.strftime("%a %b ") + str(d.day)


FAVORITE_MIN = 8
REST_DAYS = 21          # a dish made more recently than this isn't suggested (unless nothing else is left)
PICK_MODES = {"any": "Surprise me", "fav": "Favorite", "new": "Try new"}
PICK_MAX_TERMS = 5


def parse_terms(raw):
    """'chicken, Leeks,,chicken' -> ['chicken', 'Leeks']: the ingredient lists of the picker."""
    out, seen = [], set()
    for t in str(raw or "").split(","):
        t = " ".join(t.split())[:40]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:PICK_MAX_TERMS]


def _stem(w):
    """Singular, close enough to compare: tomatoes -> tomato, cherries -> cherry, leeks -> leek."""
    if len(w) > 3 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(text):
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", plain)}


def mentions(text, term):
    """Every word of `term` is a whole word of `text`, singular or plural: 'leeks' in 'Leek tart',
    'pea' not in 'Peach pie'. All Dinner Log knows about ingredients is what dinners are called."""
    want = _words(term)
    return bool(want) and want <= _words(text)


def _norm(name):
    return " ".join(str(name or "").lower().split())


def local_dishes(today, source="", rest_days=REST_DAYS, rating_min=None):
    """Every past dish eligible for a suggestion, as dicts ready for the card.

    A dish is grouped by its normalized name. Its rating is the best it has ever
    been rated (an unrated repeat doesn't demote a favorite); its recency is the
    last time it was made. Recency and best rating span every time the dish was
    made, whatever its source; the source filter only decides which dishes are
    eligible and which logged row (title, page...) the card shows.
    """
    cutoff = (today - datetime.timedelta(days=rest_days)).isoformat()
    src_cond, src_args = ("source_type = ?", [source]) if source in SOURCE_TYPES else ("1", [])
    sql = (
        "SELECT m.*, d.last_day, d.best_rating, d.all_notes FROM ("
        "  SELECT LOWER(TRIM(name)) AS norm_name, MAX(day) AS last_day, "
        "         MAX(rating) AS best_rating, GROUP_CONCAT(notes, ' ') AS all_notes, "
        f"        MAX(CASE WHEN {src_cond} THEN day END) AS show_day "
        "  FROM meals GROUP BY LOWER(TRIM(name)) "
        "  HAVING last_day <= ? AND show_day IS NOT NULL"
        + (" AND best_rating >= ?" if rating_min is not None else "") +
        ") d JOIN meals m ON LOWER(TRIM(m.name)) = d.norm_name AND m.day = d.show_day "
        f"AND {src_cond.replace('source_type', 'm.source_type')} "
        "GROUP BY d.norm_name"
    )
    params = [*src_args, cutoff, *([rating_min] if rating_min is not None else []), *src_args]
    out = []
    for row in db().execute(sql, params):
        meal = dict(row)
        meal["rating"] = meal.pop("best_rating")
        try:
            meal["days_ago"] = (today - datetime.date.fromisoformat(meal.pop("last_day"))).days
        except (ValueError, TypeError):
            meal["days_ago"] = None
        out.append(meal)
    return out


def ingredient_ok(meal, include, exclude):
    """Dinner Log's own, name-based ingredient filter. Notes count toward including
    ('added chicken') but not excluding ('no pork this time')."""
    name, notes = meal.get("name", ""), meal.get("all_notes") or meal.get("notes") or ""
    return (all(mentions(f"{name} {notes}", t) for t in include)
            and not any(mentions(name, t) for t in exclude))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect here means a login page (the address isn't the private one). Don't follow it: urllib
    would carry the Authorization header to whatever host the redirect names."""
    def redirect_request(self, *args, **kwargs):
        return None


_cookbook_opener = urllib.request.build_opener(_NoRedirect)


def fetch_cookbook_picks(mode, include=(), exclude=(), rest_days=REST_DAYS, n=10, timeout=2.0):
    """Recipes from Cookbook Collection's /admin/api/pick, or None when it isn't set up or doesn't answer
    (the picker then falls back to past dinners)."""
    if not COOKBOOK_API_URL:
        return None
    qs = urllib.parse.urlencode({"mode": mode, "with": ",".join(include), "without": ",".join(exclude),
                                 "rest": rest_days, "n": n})
    url = f"{COOKBOOK_API_URL}/admin/api/pick?{qs}"
    headers = {"Accept": "application/json"}
    if COOKBOOK_TOKEN:
        headers["Authorization"] = f"Bearer {COOKBOOK_TOKEN}"
    try:
        with _cookbook_opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data.get("recipes"), list):
            raise ValueError("no recipes in the reply")
        return data
    except Exception as exc:  # down, a login redirect, 401, bad JSON: never break the feed
        app.logger.warning("Cookbook Collection picks failed (%s): %s", COOKBOOK_API_URL, exc)
        return None


def catalog_dish(r, today):
    """A Cookbook Collection recipe in the shape the suggestion card and "Log tonight" expect."""
    online = bool(r.get("online"))
    best = r.get("best_rating")
    if isinstance(best, float) and best.is_integer():
        best = int(best)
    try:
        days_ago = (today - datetime.date.fromisoformat(r["last_cooked"])).days
    except (KeyError, TypeError, ValueError):
        days_ago = None
    return {
        "name": r.get("name") or "",
        "source_type": "online" if online else "cookbook",
        "source_title": r.get("book_title") or "",
        "source_detail": (r.get("url") if online else r.get("page")) or "",
        "rating": best,
        "days_ago": days_ago,
        "is_new": not r.get("times_cooked") and days_ago is None and best is None,
        "starred": bool(r.get("starred")),
        "from_catalog": True,
    }


def pick_for_tonight(today=None, source="", fav=False, mode=None, include=(), exclude=(), rng=random):
    """("Pick for tonight" card or None, info for the page).

    mode  fav: rated FAVORITE_MIN+; new: never cooked (Cookbook Collection only); any: a coin flip
          between those two, then anything not made lately. Without a mode, the Favorites chip decides.
    include / exclude: ingredients. Cookbook Collection matches its ingredient tags and titles; past
    dinners it doesn't know (restaurants, our own, unindexed books) are matched by name.
    """
    today = today or local_today()
    mode = mode if mode in PICK_MODES else ("fav" if fav else "any")
    info = {"mode": mode, "catalog": "off", "title_only": []}
    use_catalog = bool(COOKBOOK_API_URL) and source in ("", "cookbook", "online")
    if COOKBOOK_API_URL and not use_catalog:
        info["catalog"] = "skipped"  # a Restaurant / Our own chip is on

    def catalog(m, rest=REST_DAYS):
        if not use_catalog:
            return None
        data = fetch_cookbook_picks(m, include, exclude, rest)
        info["catalog"] = "down" if data is None else "ok"
        if data is None:
            return None
        info["title_only"] = data.get("title_only") or []
        dishes = [catalog_dish(r, today) for r in data["recipes"]]
        return {"dishes": [d for d in dishes if not source or d["source_type"] == source],
                "linked": {_norm(x) for x in data.get("linked_dinners") or []}}

    def local(rest, rating_min, skip=frozenset()):
        return [m for m in local_dishes(today, source, rest, rating_min)
                if _norm(m["name"]) not in skip and ingredient_ok(m, include, exclude)]

    def favorites(rest=REST_DAYS):
        # Dishes Cookbook Collection has matched to a recipe are left to it: it knows their tags.
        cat = catalog("fav", rest)
        pool = local(rest, FAVORITE_MIN, cat["linked"] if cat else frozenset())
        return pool + (cat["dishes"] if cat else [])

    def something_new():
        cat = catalog("new")
        return cat["dishes"][:1] if cat else []  # its list is already a weighted draw; take the top

    if mode == "new":
        order = [something_new]
    elif mode == "fav":
        order = [favorites, lambda: local(2, FAVORITE_MIN)]
    else:
        first = [favorites, something_new] if not use_catalog or rng.random() < 0.5 else [something_new, favorites]
        order = first + [lambda: local(REST_DAYS, None), lambda: local(2, None)]
    for step in order:
        pool = step()
        if pool:
            return rng.choice(pool), info
    return None, info


def suggest_dinner(today=None, source="", fav=False, **kw):
    """Just the card; see pick_for_tonight."""
    return pick_for_tonight(today, source, fav, **kw)[0]


PAGE_SIZE = 100  # dinners per feed page


def parse_cursor(raw):
    """'YYYY-MM-DD.<id>' -> (day, id), or None if malformed."""
    day, _, mid = str(raw).partition(".")
    try:
        return datetime.date.fromisoformat(day).isoformat(), int(mid)
    except ValueError:
        return None


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "").strip()
    fav = request.args.get("fav", "").strip()
    suggest = request.args.get("suggest", "").strip()

    cur_source = source if source in SOURCE_TYPES else ""
    cur_fav = "1" if fav == "1" else ""
    pick_mode = request.args.get("mode", "").strip()
    pick_mode = pick_mode if pick_mode in PICK_MODES else ""
    include = parse_terms(request.args.get("with"))
    exclude = parse_terms(request.args.get("without"))
    # carried by every link on the page, so filtering the feed doesn't forget the picker's settings
    pick_state = {"mode": pick_mode or None, "with": ", ".join(include) or None,
                  "without": ", ".join(exclude) or None}

    sql = ("SELECT m.*, (SELECT filename FROM photos p WHERE p.meal_id = m.id "
           "ORDER BY p.id LIMIT 1) AS thumb FROM meals m WHERE 1=1")
    args = []
    if q:
        sql += (" AND (m.name LIKE ? OR m.source_title LIKE ? "
                "OR m.source_detail LIKE ? OR m.notes LIKE ?)")
        args.extend([f"%{q}%"] * 4)
    if cur_source:
        sql += " AND m.source_type = ?"
        args.append(cur_source)
    if cur_fav == "1":
        sql += " AND m.rating >= ?"
        args.append(FAVORITE_MIN)

    cursor = parse_cursor(request.args.get("before", ""))
    if cursor:
        sql += " AND (m.day < ? OR (m.day = ? AND m.id < ?))"
        args.extend([cursor[0], cursor[0], cursor[1]])

    # Keyset paging on (day, id): fetch one extra row to know if there's more.
    sql += " ORDER BY m.day DESC, m.id DESC LIMIT ?"
    meals = db().execute(sql, [*args, PAGE_SIZE + 1]).fetchall()
    older = None
    if len(meals) > PAGE_SIZE:
        meals = meals[:PAGE_SIZE]
        last = meals[-1]
        older = url_for("index", q=q or None, source=cur_source or None,
                        fav=cur_fav or None, before=f"{last['day']}.{last['id']}")
    missing_days = recent_missing_days() if not q and not cursor else []
    suggestion, pick_info = (pick_for_tonight(source=cur_source, fav=cur_fav == "1", mode=pick_mode or None,
                                              include=include, exclude=exclude)
                             if suggest == "1" else (None, None))

    return render_template(
        "index.html",
        meals=meals,
        q=q,
        types=SOURCE_TYPES,
        missing_days=missing_days,
        cur_source=cur_source,
        cur_fav=cur_fav,
        suggestion=suggestion,
        pick_info=pick_info,
        pick_state=pick_state,
        pick_modes=PICK_MODES,
        cur_mode=pick_mode or ("fav" if cur_fav == "1" else "any"),
        older=older,
    )



@app.route("/new", methods=["GET", "POST"])
def new_meal():
    if request.method == "POST":
        v = form_values()
        cur = db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, source_detail, notes, rating, "
            "cookbook_book_id, cookbook_recipe_id) VALUES (:day, :name, :source_type, :source_title, "
            ":source_detail, :notes, :rating, :cookbook_book_id, :cookbook_recipe_id)", v)
        skipped = save_photos(cur.lastrowid, request.files.getlist("photos"))
        db().commit()
        return redirect(url_for("meal_detail", meal_id=cur.lastrowid, skipped=skipped or None))
    meal = parse_meal_params(request.args)
    return render_template("form.html", meal=meal, photos=[], types=SOURCE_TYPES,
                           books=titles_for("cookbook"), places=titles_for("restaurant"),
                           editing=False)


def get_meal(meal_id):
    meal = db().execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
    if meal is None:
        abort(404)
    photos = db().execute("SELECT * FROM photos WHERE meal_id = ? ORDER BY id", (meal_id,)).fetchall()
    return meal, photos


@app.route("/meal/<int:meal_id>")
def meal_detail(meal_id):
    meal, photos = get_meal(meal_id)
    skipped = request.args.get("skipped", "")
    return render_template("detail.html", meal=meal, photos=photos, types=SOURCE_TYPES,
                           skipped=int(skipped) if skipped.isdigit() else 0)


@app.route("/meal/<int:meal_id>/edit", methods=["GET", "POST"])
def edit_meal(meal_id):
    meal, photos = get_meal(meal_id)
    if request.method == "POST":
        v = form_values()
        v["id"] = meal_id
        db().execute(
            "UPDATE meals SET day=:day, name=:name, source_type=:source_type, "
            "source_title=:source_title, source_detail=:source_detail, notes=:notes, "
            "rating=:rating, cookbook_book_id=:cookbook_book_id, cookbook_recipe_id=:cookbook_recipe_id "
            "WHERE id=:id", v)
        drop = [int(x) for x in request.form.getlist("delete_photo") if x.isdigit()]
        names = []
        if drop:
            marks = ",".join("?" * len(drop))
            names = [r["filename"] for r in db().execute(
                f"SELECT filename FROM photos WHERE meal_id=? AND id IN ({marks})",
                [meal_id, *drop])]
            db().execute(f"DELETE FROM photos WHERE meal_id=? AND id IN ({marks})", [meal_id, *drop])
        skipped = save_photos(meal_id, request.files.getlist("photos"))
        db().commit()
        remove_photo_files(names)  # only once the rows are gone for good
        return redirect(url_for("meal_detail", meal_id=meal_id, skipped=skipped or None))
    return render_template("form.html", meal=meal, photos=photos, types=SOURCE_TYPES,
                           books=titles_for("cookbook"), places=titles_for("restaurant"),
                           editing=True)


@app.route("/meal/<int:meal_id>/delete", methods=["POST"])
def delete_meal(meal_id):
    get_meal(meal_id)
    names = [r["filename"] for r in db().execute(
        "SELECT filename FROM photos WHERE meal_id=?", (meal_id,))]
    db().execute("DELETE FROM meals WHERE id=?", (meal_id,))
    db().commit()
    remove_photo_files(names)
    return redirect(url_for("index"))


@app.route("/photos/<path:filename>")
def photo(filename):
    resp = send_from_directory(PHOTO_DIR, filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
