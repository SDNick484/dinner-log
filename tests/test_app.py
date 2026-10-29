import datetime
import importlib
import io
import os
import sqlite3
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_app(data_dir):
    """Import a fresh copy of the app bound to data_dir (the app reads DATA_DIR at import)."""
    os.environ["DATA_DIR"] = str(data_dir)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.modules.pop("app", None)
    return importlib.import_module("app")


@pytest.fixture()
def client(tmp_path):
    return load_app(tmp_path).app.test_client()


def jpeg(size=(4000, 3000)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "JPEG")
    buf.seek(0)
    return buf


def text(resp):
    return resp.get_data(as_text=True)


def test_create_with_photo_and_view(client, tmp_path):
    r = client.post("/new", data={
        "day": "2026-09-30", "name": "Pad thai", "source_type": "cookbook",
        "source_title": "Thai Food", "source_detail": "p. 88", "notes": "great",
        "photos": [(jpeg(), "a.jpg"), (io.BytesIO(b"not an image"), "b.txt")],
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    page = text(client.get(r.headers["Location"]))
    assert "Pad thai" in page and "Thai Food" in page and "p. 88" in page
    photos = os.listdir(tmp_path / "photos")
    assert len(photos) == 1                      # the non-image upload is skipped
    assert Image.open(tmp_path / "photos" / photos[0]).size == (1600, 1200)  # downscaled


def test_restaurant_source_and_rating(client):
    r = client.post("/new", data={
        "day": "2026-10-02", "name": "Burger", "source_type": "restaurant",
        "source_title": "Luigi's", "source_detail": "Springfield", "rating": "9"})
    page = text(client.get(r.headers["Location"]))
    assert "Restaurant" in page and "Luigi" in page and "Springfield" in page and "★ 9" in page
    assert "★ 9/10" in text(client.get("/"))
    assert "Luigi" in text(client.get("/new"))   # offered for autocomplete


@pytest.mark.parametrize("bad", ["0", "11", "-1", "abc", ""])
def test_invalid_rating_is_ignored(client, bad):
    r = client.post("/new", data={"name": "x", "rating": bad})
    assert "★" not in text(client.get(r.headers["Location"]))


def test_edit_rating_and_clear(client):
    loc = client.post("/new", data={"name": "Soup", "rating": "5"}).headers["Location"]
    client.post(loc + "/edit", data={"name": "Soup", "rating": "8"})
    assert "★ 8" in text(client.get(loc))
    client.post(loc + "/edit", data={"name": "Soup"})
    assert "★" not in text(client.get(loc))


def test_search(client):
    client.post("/new", data={"name": "Pad thai", "day": "2026-09-30"})
    client.post("/new", data={"name": "Tacos", "day": "2026-10-01"})
    hits = text(client.get("/?q=thai"))
    assert "Pad thai" in hits and "Tacos" not in hits


def test_remove_photo_and_delete(client, tmp_path):
    loc = client.post("/new", data={"name": "Stew", "photos": [(jpeg((800, 600)), "a.jpg")]},
                      content_type="multipart/form-data").headers["Location"]
    assert len(os.listdir(tmp_path / "photos")) == 1
    client.post(loc + "/edit", data={"name": "Stew", "delete_photo": "1"},
                content_type="multipart/form-data")
    assert os.listdir(tmp_path / "photos") == []
    assert client.post(loc + "/delete").status_code == 302
    assert client.get(loc).status_code == 404


def test_upgrades_database_created_before_ratings(tmp_path):
    con = sqlite3.connect(tmp_path / "meals.db")
    con.executescript("""
        CREATE TABLE meals (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, name TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'original', source_title TEXT NOT NULL DEFAULT '',
            source_detail TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE, filename TEXT NOT NULL);
        INSERT INTO meals (day, name, source_type) VALUES ('2026-09-30', 'Old tacos', 'online');""")
    con.commit()
    con.close()
    client = load_app(tmp_path).app.test_client()
    assert "Old tacos" in text(client.get("/"))          # existing rows survive
    loc = client.post("/new", data={"name": "New", "rating": "7"}).headers["Location"]
    assert "★ 7" in text(client.get(loc))
    with sqlite3.connect(tmp_path / "meals.db") as con:
        indices = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='photos'"
        ).fetchall()]
    assert "idx_photos_meal_id" in indices



def test_pwa_assets(client):
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/manifest.json").status_code == 200
    assert 'crossorigin="use-credentials"' in text(client.get("/"))


def test_photos_meal_id_index_created(tmp_path):
    load_app(tmp_path)
    with sqlite3.connect(tmp_path / "meals.db") as con:
        indices = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='photos'"
        ).fetchall()]
    assert "idx_photos_meal_id" in indices


def test_new_meal_prefill_from_query_params(client):
    resp = client.get("/new?day=2026-05-15&name=Tacos&source_type=cookbook&source_title=Mexican+Everyday&source_detail=p.+45&rating=9&notes=Delicious")
    assert resp.status_code == 200
    html = text(resp)
    assert 'value="2026-05-15"' in html
    assert 'value="Tacos"' in html
    assert 'value="Mexican Everyday"' in html
    assert 'value="p. 45"' in html
    assert 'Delicious</textarea>' in html
    assert 'value="9" checked' in html



def test_new_meal_prefill_invalid_params_fallback(client):
    resp = client.get("/new?day=not-a-date&source_type=invalid&rating=99")
    assert resp.status_code == 200
    html = text(resp)
    today = sys.modules["app"].local_today()  # the app's "today" (APP_TZ), not this machine's
    assert f'value="{today.isoformat()}"' in html
    assert 'value="original" checked' in html
    assert 'checked' not in [line for line in html.splitlines() if 'name="rating"' in line and 'checked' in line]


def test_quick_date_buttons_rendered(client):
    resp = client.get("/new")
    assert resp.status_code == 200
    html = text(resp)
    assert 'data-quick-date="today"' in html
    assert 'data-quick-date="yesterday"' in html


def test_recent_missing_days_calculation(client, tmp_path):
    app_module = load_app(tmp_path)
    today = datetime.date(2026, 10, 28)
    with app_module.app.app_context():
        missing = app_module.recent_missing_days(days=7, today=today)
        assert len(missing) == 7
        assert missing[0] == "2026-10-28"
        assert missing[-1] == "2026-10-22"

        app_module.db().execute(
            "INSERT INTO meals (day, name) VALUES ('2026-10-28', 'Curry'), ('2026-10-26', 'Pizza')"
        )
        app_module.db().commit()

        missing = app_module.recent_missing_days(days=7, today=today)
        assert len(missing) == 5
        assert "2026-10-28" not in missing
        assert "2026-10-26" not in missing
        assert "2026-10-27" in missing


def test_gap_label_filter(client, tmp_path):
    app_module = load_app(tmp_path)
    today = datetime.date(2026, 10, 28)
    yesterday = datetime.date(2026, 10, 27)
    past = datetime.date(2026, 10, 25)
    with app_module.app.app_context():
        assert app_module.gap_label(today.isoformat(), today=today) == "Today"
        assert app_module.gap_label(yesterday.isoformat(), today=today) == "Yesterday"
        assert app_module.gap_label(past.isoformat(), today=today) == "Sun Oct 25"


def test_catch_up_pills_rendered_on_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = text(resp)
    assert 'class="gaps"' in html
    assert 'Catch up:' in html
    assert 'class="gap-pill"' in html
    assert f'/new?day={sys.modules["app"].local_today().isoformat()}' in html


def test_catch_up_pills_hidden_when_all_logged(client):
    today = sys.modules["app"].local_today()
    for i in range(7):
        d = (today - datetime.timedelta(days=i)).isoformat()
        client.post("/new", data={"day": d, "name": f"Dinner {i}"})
    resp = client.get("/")
    html = text(resp)
    assert 'class="gaps"' not in html


def test_catch_up_pills_hidden_during_search(client):
    resp = client.get("/?q=anything")
    html = text(resp)
    assert 'class="gaps"' not in html


def test_recent_missing_days_edge_cases_and_year_boundary(client, tmp_path):
    app_module = load_app(tmp_path)
    with app_module.app.app_context():
        assert app_module.recent_missing_days(days=0) == []
        assert app_module.recent_missing_days(days=-5) == []

        jan3 = datetime.date(2026, 1, 3)
        missing = app_module.recent_missing_days(days=7, today=jan3)
        assert len(missing) == 7
        assert missing[0] == "2026-01-03"
        assert missing[-1] == "2025-12-28"

        app_module.db().execute(
            "INSERT INTO meals (day, name) VALUES ('2025-12-31', 'NYE Feast')"
        )
        app_module.db().commit()
        missing = app_module.recent_missing_days(days=7, today=jan3)
        assert len(missing) == 6
        assert "2025-12-31" not in missing


def test_filter_by_source_and_favorites(client):
    client.post("/new", data={"name": "Pasta", "source_type": "original", "rating": "9", "day": "2026-10-01"})
    client.post("/new", data={"name": "Curry", "source_type": "cookbook", "source_title": "Thai Food", "rating": "9", "day": "2026-10-02"})
    client.post("/new", data={"name": "Burger", "source_type": "restaurant", "source_title": "Diner", "rating": "6", "day": "2026-10-03"})

    # Filter by source=cookbook
    r = client.get("/?source=cookbook")
    assert "Curry" in text(r)
    assert "Pasta" not in text(r)
    assert "Burger" not in text(r)

    # Filter by fav=1 (rating >= 8)
    r = client.get("/?fav=1")
    assert "Pasta" in text(r)
    assert "Curry" in text(r)
    assert "Burger" not in text(r)

    # Combined source=cookbook & fav=1
    r = client.get("/?source=cookbook&fav=1")
    assert "Curry" in text(r)
    assert "Pasta" not in text(r)

    # Invalid source is ignored gracefully
    r = client.get("/?source=nonexistent")
    assert r.status_code == 200
    assert "Pasta" in text(r)
    assert "Curry" in text(r)
    assert "Burger" in text(r)

    # Combined with search query q
    r = client.get("/?q=Pasta&source=original")
    assert '<div class="name">Pasta</div>' in text(r)
    r = client.get("/?q=Pasta&source=cookbook")
    assert '<div class="name">Pasta</div>' not in text(r)
    assert 'No dinners match' in text(r)


def test_suggest_dinner_logic(client, tmp_path):
    app_module = load_app(tmp_path)
    today = datetime.date(2026, 10, 28)
    with app_module.app.app_context():
        # Empty DB returns None
        assert app_module.suggest_dinner(today=today) is None

        # Add recent meal (yesterday) and older favorite (30 days ago)
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, rating) "
            "VALUES ('2026-10-27', 'Recent Stew', 'original', '', 9)"
        )
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, source_detail, rating) "
            "VALUES ('2026-09-20', 'Old Lasagna', 'cookbook', 'Italian Bible', 'p. 110', 9)"
        )
        app_module.db().commit()

        # Should pick the older favorite
        s = app_module.suggest_dinner(today=today)
        assert s is not None
        assert s["name"] == "Old Lasagna"
        assert s["days_ago"] == 38

        # Edge case: If Old Lasagna was also made yesterday, it must NOT be suggested
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, rating) "
            "VALUES ('2026-10-27', 'Old Lasagna', 'cookbook', 'Italian Bible', 9)"
        )
        app_module.db().commit()
        # Now both Recent Stew and Old Lasagna were made yesterday; nothing eligible
        assert app_module.suggest_dinner(today=today) is None

        # Priority 2: Non-favorite older than 21 days (e.g. rating 5, 25 days ago)
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, rating) "
            "VALUES ('2026-10-03', 'Simple Broth', 'original', 5)"
        )
        app_module.db().commit()
        s2 = app_module.suggest_dinner(today=today)
        assert s2 is not None
        assert s2["name"] == "Simple Broth"
        assert s2["days_ago"] == 25

        # Priority 3: No meals older than 21 days, but meal made 5 days ago
        app_module.db().execute("DELETE FROM meals")
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, rating) "
            "VALUES ('2026-10-23', 'Quick Stir Fry', 'original', 7)"
        )
        app_module.db().commit()
        s3 = app_module.suggest_dinner(today=today)
        assert s3 is not None
        assert s3["name"] == "Quick Stir Fry"
        assert s3["days_ago"] == 5

        # Meal made today or yesterday is NEVER suggested
        app_module.db().execute("DELETE FROM meals")
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, rating) "
            "VALUES ('2026-10-28', 'Today Salad', 'original', 10)"
        )
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, rating) "
            "VALUES ('2026-10-27', 'Yesterday Pizza', 'original', 10)"
        )
        app_module.db().commit()
        assert app_module.suggest_dinner(today=today) is None


def test_filter_empty_state_and_chip_sanitization(client):
    client.post("/new", data={"name": "Pasta", "source_type": "original", "rating": "6", "day": "2026-10-01"})

    # Filter with no results shows filter empty state, NOT "Nothing logged yet"
    r = client.get("/?source=cookbook")
    html = text(r)
    assert "No dinners match your filter." in html
    assert "Nothing logged yet" not in html

    r = client.get("/?fav=1")
    html = text(r)
    assert "No dinners match your filter." in html
    assert "Nothing logged yet" not in html

    # Unsanitized source falls back to All active
    r = client.get("/?source=martian")
    html = text(r)
    assert '<a class="chip-btn active"\n       href="/">All</a>' in html or '<a class="chip-btn active" href="/">All</a>' in html or 'class="chip-btn active"' in html



def test_filter_chips_and_suggestion_card_rendering(client):
    client.post("/new", data={
        "name": "Tacos",
        "source_type": "cookbook",
        "source_title": "Mexican Everyday",
        "source_detail": "p. 42",
        "rating": "9",
        "day": "2026-09-01"
    })
    resp = client.get("/")
    html = text(resp)
    assert 'class="filter-bar"' in html
    assert 'All' in html
    assert 'Favorites (★ 8+)' in html
    assert 'Pick for tonight' in html

    # With suggest=1
    resp = client.get("/?suggest=1")
    html = text(resp)
    assert 'class="suggest-card"' in html
    assert 'Tacos' in html
    assert 'Mexican Everyday' in html
    assert 'p. 42' in html
    assert 'Roll again' in html
    assert 'Log tonight' in html
    assert 'source_type=cookbook' in html
    assert 'source_title=Mexican+Everyday' in html or 'source_title=Mexican%20Everyday' in html
    assert 'source_detail=p.+42' in html or 'source_detail=p.%2042' in html
    assert 'class="suggest-dismiss"' in html


def test_cookbook_search_url_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKBOOK_URL", "https://cookbooks.example.com")
    app_module = load_app(tmp_path)
    with app_module.app.app_context():
        assert app_module.cookbook_search_url("Thai Food") == "https://cookbooks.example.com/#/?q=Thai+Food"
        assert app_module.cookbook_search_url("Salt & Pepper") == "https://cookbooks.example.com/#/?q=Salt+%26+Pepper"
        assert app_module.cookbook_search_url("   ") == ""
        assert app_module.cookbook_search_url(None) == ""

    # When unset
    monkeypatch.delenv("COOKBOOK_URL", raising=False)
    app_module_unset = load_app(tmp_path)
    with app_module_unset.app.app_context():
        assert app_module_unset.cookbook_search_url("Thai Food") == ""


def test_titles_for_cookbook_merges_remote_and_handles_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKBOOK_URL", "http://fake-cookbooks.local")
    app_module = load_app(tmp_path)

    # Insert local meals
    with app_module.app.app_context():
        app_module.db().execute(
            "INSERT INTO meals (day, name, source_type, source_title) "
            "VALUES ('2026-10-01', 'Pasta', 'cookbook', 'Italian Bible')"
        )
        app_module.db().commit()

        # Mock urllib.request.urlopen returning books JSON
        class FakeResponse:
            def read(self):
                return b'{"books": [{"title": "Thai Food"}, {"title": "italian bible"}, {"title": "Zuni Cafe"}]}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        calls = []
        def fake_urlopen(url, timeout=None):
            calls.append(url)
            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        titles = app_module.titles_for("cookbook")
        # Merged, case-insensitively deduplicated, sorted
        assert titles == ["Italian Bible", "Thai Food", "Zuni Cafe"]
        assert len(calls) == 1

        # Cache test: second call within TTL should NOT call urlopen again
        titles2 = app_module.titles_for("cookbook")
        assert titles2 == titles
        assert len(calls) == 1

        # Error fallback test: when urlopen raises URLError or timeout
        import urllib.error
        fail_calls = []
        def failing_urlopen(url, timeout=None):
            fail_calls.append(url)
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)
        app_module._cookbook_cache["cached_at"] = 0  # expire cache
        # During outage, serves stale cache if available
        stale_titles = app_module.titles_for("cookbook")
        assert stale_titles == ["Italian Bible", "Thai Food", "Zuni Cafe"]
        assert len(fail_calls) == 1

        # Backoff: immediate next call does NOT call urlopen again
        app_module.titles_for("cookbook")
        assert len(fail_calls) == 1

        # When cache is completely empty and fetch fails, falls back to local DB titles
        app_module._cookbook_cache["titles"] = []
        app_module._cookbook_cache["cached_at"] = 0
        fallback_titles = app_module.titles_for("cookbook")
        assert fallback_titles == ["Italian Bible"]


def test_cookbook_links_rendered_when_configured(client, monkeypatch):
    monkeypatch.setattr("app.COOKBOOK_URL", "https://cookbooks.example.com")
    monkeypatch.setattr("app.fetch_cookbook_titles", lambda: ["Thai Food"])

    # Create a meal from a cookbook
    client.post("/new", data={
        "name": "Panang Curry",
        "source_type": "cookbook",
        "source_title": "Thai Food",
        "source_detail": "p. 110",
        "day": "2026-09-01"
    })

    # Detail page: check outbound links
    detail_html = text(client.get("/meal/1"))
    assert 'href="https://cookbooks.example.com/#/?q=Thai+Food"' in detail_html
    assert 'href="https://cookbooks.example.com/#/?q=Panang+Curry"' in detail_html
    assert 'Search recipe in Cookbook Collection ↗' in detail_html

    # Form: check Browse catalog link
    form_html = text(client.get("/new"))
    assert 'href="https://cookbooks.example.com/#/"' in form_html
    assert 'Browse catalog ↗' in form_html

    # Home feed suggestion card: check View in Cookbook Collection
    suggest_html = text(client.get("/?suggest=1"))
    assert 'href="https://cookbooks.example.com/#/?q=Thai+Food"' in suggest_html
    assert 'View in Cookbook Collection ↗' in suggest_html

    # When COOKBOOK_URL is unset/empty
    monkeypatch.setattr("app.COOKBOOK_URL", "")
    detail_unset = text(client.get("/meal/1"))
    assert 'https://cookbooks.example.com' not in detail_unset
    assert 'Search recipe in Cookbook Collection' not in detail_unset

    form_unset = text(client.get("/new"))
    assert 'Browse catalog ↗' not in form_unset


def test_local_today_uses_app_tz(monkeypatch):
    import datetime as dt
    from zoneinfo import ZoneInfo
    import app as A
    monkeypatch.setattr(A, "APP_TZ", ZoneInfo("America/Los_Angeles"))
    evening = dt.datetime(2026, 10, 29, 2, 30, tzinfo=dt.timezone.utc)  # 7:30pm PDT on the 28th
    assert A.local_today(evening) == dt.date(2026, 10, 28)
    monkeypatch.setattr(A, "APP_TZ", ZoneInfo("UTC"))
    assert A.local_today(evening) == dt.date(2026, 10, 29)


def test_cookbook_url_without_scheme_gets_one(tmp_path, monkeypatch):
    # A bare host made the catalog links relative: /cookbooks.example.com/#/?q=...
    monkeypatch.setenv("COOKBOOK_URL", "cookbooks.example.com/")
    monkeypatch.delenv("COOKBOOK_API_URL", raising=False)
    a = load_app(tmp_path)
    assert a.COOKBOOK_URL == a.COOKBOOK_API_URL == "https://cookbooks.example.com"
    assert a.cookbook_search_url("Pulled pork") == "https://cookbooks.example.com/#/?q=Pulled+pork"
    assert a.base_url("127.0.0.1:8767") == "http://127.0.0.1:8767"
    assert a.base_url("localhost:8767") == "http://localhost:8767"
    assert a.base_url("192.168.1.20:8767") == "http://192.168.1.20:8767"
    assert a.base_url("http://cookbooks.example.com/") == "http://cookbooks.example.com"
    assert a.base_url("") == ""
    monkeypatch.delenv("COOKBOOK_URL")
    load_app(tmp_path)
