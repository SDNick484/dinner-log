"""Dinner Log: a tiny self-hosted app for tracking family dinners.

Flask + SQLite, photos stored on disk. No auth: run it behind your LAN/VPN.
"""
import datetime
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from flask import (Flask, abort, g, redirect, render_template, request,
                   send_from_directory, url_for)
from PIL import Image, ImageOps

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
DB_PATH = os.path.join(DATA_DIR, "meals.db")
os.makedirs(PHOTO_DIR, exist_ok=True)

SOURCE_TYPES = {"cookbook": "Cookbook", "online": "Online",
                "restaurant": "Restaurant", "original": "Our own"}
APP_TZ = ZoneInfo(os.environ.get("APP_TZ", "UTC"))  # IANA name, e.g. America/Chicago
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    authenticating proxy's cookie may ride along on a cross-site form post.
    Browsers label such requests; refuse them. Requests without the headers
    (curl, scripts, very old browsers) pass, since they can't carry a victim's cookie
    from another site anyway."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)
    origin = request.headers.get("Origin")
    if origin and (urllib.parse.urlsplit(origin).netloc != request.host
                   and origin.rstrip("/") not in TRUSTED_ORIGINS):
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


def parse_meal_params(params, default_source="cookbook"):
    if "source_type" in params:
        stype = str(params.get("source_type") or "")
        if stype not in SOURCE_TYPES:
            stype = "original"
    else:
        stype = default_source
    day = str(params.get("day") or "").strip() or local_today().isoformat()
    try:
        datetime.date.fromisoformat(day)
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
    }



def form_values():
    v = parse_meal_params(request.form, default_source="original")
    if not v["name"]:
        v["name"] = "Untitled dinner"
    return v



COOKBOOK_URL = os.environ.get("COOKBOOK_URL", "").strip().rstrip("/")
_cookbook_cache = {"cached_at": 0, "titles": []}


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
    if not COOKBOOK_URL:
        return []
    now = time.time()
    if now - _cookbook_cache["cached_at"] < 60:
        return _cookbook_cache["titles"]

    req_url = f"{COOKBOOK_URL}/api/books"
    try:
        with urllib.request.urlopen(req_url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            books = data.get("books", [])
            titles = [b["title"].strip() for b in books if b.get("title")]
            _cookbook_cache["titles"] = titles
            _cookbook_cache["cached_at"] = now
            return titles
    except Exception as exc:
        app.logger.warning("Failed to fetch books from Cookbook Collection (%s): %s", req_url, exc)
        _cookbook_cache["cached_at"] = now - 45
        return _cookbook_cache["titles"]


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


def suggest_dinner(today=None, source="", fav=False):
    """Pick a past dinner worth repeating.

    A dish is grouped by its normalized name. Its rating is the best it has ever
    been rated (an unrated repeat doesn't demote a favorite); its recency is the
    last time it was made. `source` / `fav` mirror the feed's filter chips.
    """
    if today is None:
        today = local_today()
    cutoff_21 = (today - datetime.timedelta(days=21)).isoformat()
    cutoff_2 = (today - datetime.timedelta(days=2)).isoformat()

    # Recency and best rating span every time the dish was made, whatever its
    # source; the source filter only decides which dishes are eligible and
    # which logged row (title, page...) the card shows.
    src_cond, src_args = ("source_type = ?", [source]) if source in SOURCE_TYPES else ("1", [])

    def query_candidate(cutoff_date, rating_min=None):
        sql = (
            "SELECT m.*, d.last_day, d.best_rating FROM ("
            "  SELECT LOWER(TRIM(name)) AS norm_name, MAX(day) AS last_day, "
            "         MAX(rating) AS best_rating, "
            f"        MAX(CASE WHEN {src_cond} THEN day END) AS show_day "
            "  FROM meals GROUP BY LOWER(TRIM(name)) "
            "  HAVING last_day <= ? AND show_day IS NOT NULL"
            + (" AND best_rating >= ?" if rating_min is not None else "") +
            ") d JOIN meals m ON LOWER(TRIM(m.name)) = d.norm_name AND m.day = d.show_day "
            f"AND {src_cond.replace('source_type', 'm.source_type')} "
            "GROUP BY d.norm_name ORDER BY RANDOM() LIMIT 1"
        )
        params = [*src_args, cutoff_date]
        if rating_min is not None:
            params.append(rating_min)
        params.extend(src_args)
        return db().execute(sql, params).fetchone()

    floor = FAVORITE_MIN if fav else None
    # Priority 1: a favorite not made in the last 21 days
    row = query_candidate(cutoff_21, rating_min=FAVORITE_MIN)
    # Priority 2: anything (within the filters) not made in the last 21 days
    if row is None:
        row = query_candidate(cutoff_21, rating_min=floor)
    # Priority 3: anything (within the filters) not made in the last 2 days
    if row is None:
        row = query_candidate(cutoff_2, rating_min=floor)
    if row is None:
        return None

    meal_dict = dict(row)
    meal_dict["rating"] = meal_dict.pop("best_rating")
    try:
        d = datetime.date.fromisoformat(meal_dict.pop("last_day"))
        meal_dict["days_ago"] = (today - d).days
    except (ValueError, TypeError):
        meal_dict["days_ago"] = None
    return meal_dict


PAGE_SIZE = 100  # dinners per feed page


def parse_cursor(raw):
    """'YYYY-MM-DD.<id>' -> (day, id), or None if malformed."""
    day, _, mid = str(raw).partition(".")
    try:
        datetime.date.fromisoformat(day)
        return day, int(mid)
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
    suggestion = (suggest_dinner(source=cur_source, fav=cur_fav == "1")
                  if suggest == "1" else None)

    return render_template(
        "index.html",
        meals=meals,
        q=q,
        types=SOURCE_TYPES,
        missing_days=missing_days,
        cur_source=cur_source,
        cur_fav=cur_fav,
        suggestion=suggestion,
        older=older,
    )



@app.route("/new", methods=["GET", "POST"])
def new_meal():
    if request.method == "POST":
        v = form_values()
        cur = db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, source_detail, notes, rating) "
            "VALUES (:day, :name, :source_type, :source_title, :source_detail, :notes, :rating)", v)
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
            "rating=:rating WHERE id=:id", v)
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
