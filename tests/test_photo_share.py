"""Sharing a dinner photo with Cookbook Collection: the `shared` flag, the edit-form toggle, the `photos` field
in /api/history and the token-only GET /api/photos/<file> that serves shared files and nothing else."""
import sqlite3

import pytest

from test_app import jpeg, load_app

TOKEN = "t0ken-for-tests"
COOKBOOK = {"source_type": "cookbook", "source_title": "Zuni", "source_detail": "342"}


@pytest.fixture()
def share(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "HISTORY_TOKEN", TOKEN)
    return a, a.app.test_client()


def add_meal(c, n_photos=1, **fields):
    data = {"day": "2026-10-01", "name": "Roast chicken", **COOKBOOK, **fields}
    if n_photos:
        data["photos"] = [(jpeg((800, 600)), f"p{i}.jpg") for i in range(n_photos)]
    r = c.post("/new", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1].split("?")[0])


def photo_rows(a, meal_id):
    con = sqlite3.connect(a.DB_PATH)
    try:
        return con.execute("SELECT id, filename, shared FROM photos WHERE meal_id=? ORDER BY id", (meal_id,)).fetchall()
    finally:
        con.close()


def edit(c, meal_id, share_ids=(), **fields):
    data = {"day": "2026-10-01", "name": "Roast chicken", **COOKBOOK, **fields,
            "share_photo": [str(i) for i in share_ids]}
    r = c.post(f"/meal/{meal_id}/edit", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    return r


def history(c, etag=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if etag:
        headers["If-None-Match"] = etag
    return c.get("/api/history", headers=headers)


def fetch(c, name, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return c.get(f"/api/photos/{name}", headers=headers)


# ------------------------------------------------------------------ schema

def test_upgrade_adds_shared_column_private_by_default(tmp_path):
    con = sqlite3.connect(tmp_path / "meals.db")
    con.executescript("""
        CREATE TABLE meals (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, name TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'original', source_title TEXT NOT NULL DEFAULT '',
            source_detail TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE, filename TEXT NOT NULL);
        INSERT INTO meals (day, name, source_type) VALUES ('2026-10-01', 'Old soup', 'cookbook');
        INSERT INTO photos (meal_id, filename) VALUES (1, 'aaaa.jpg');
    """)
    con.commit()
    con.close()
    a = load_app(tmp_path)
    assert photo_rows(a, 1) == [(1, "aaaa.jpg", 0)]
    load_app(tmp_path)                                    # the upgrade is idempotent
    assert photo_rows(a, 1) == [(1, "aaaa.jpg", 0)]


def test_new_photos_are_private(share):
    a, c = share
    mid = add_meal(c, 2)
    assert [r[2] for r in photo_rows(a, mid)] == [0, 0]


# ------------------------------------------------------------------ toggle

def test_toggle_shares_and_unshares_per_photo(share):
    a, c = share
    mid = add_meal(c, 2)
    (p1, _, _), (p2, _, _) = photo_rows(a, mid)
    edit(c, mid, [p2])
    assert [r[2] for r in photo_rows(a, mid)] == [0, 1]
    edit(c, mid, [p1])                                    # an unchecked box unshares
    assert [r[2] for r in photo_rows(a, mid)] == [1, 0]
    edit(c, mid)
    assert [r[2] for r in photo_rows(a, mid)] == [0, 0]


def test_toggle_ignores_other_meals_photos(share):
    a, c = share
    mine, theirs = add_meal(c), add_meal(c, name="Other dish")
    their_photo = photo_rows(a, theirs)[0][0]
    edit(c, mine, [their_photo, "x", -1])
    assert photo_rows(a, theirs)[0][2] == 0


def test_photos_uploaded_with_the_edit_start_private(share):
    a, c = share
    mid = add_meal(c, 1)
    p1 = photo_rows(a, mid)[0][0]
    data = {"day": "2026-10-01", "name": "Roast chicken", **COOKBOOK, "share_photo": [str(p1), str(p1 + 1)],
            "photos": [(jpeg((800, 600)), "new.jpg")]}
    c.post(f"/meal/{mid}/edit", data=data, content_type="multipart/form-data")
    assert [r[2] for r in photo_rows(a, mid)] == [1, 0]  # the new photo's id was guessed, not shown: still private


@pytest.mark.parametrize("source", [{"source_type": "online", "source_title": "Site",
                                     "source_detail": "https://cooking.example.com/1"},
                                    {"source_type": "cookbook", "source_title": "Zuni", "source_detail": "",
                                     "cookbook_book_uid": "a" * 32}])
def test_eligible_sources(share, source):
    a, c = share
    mid = add_meal(c, **source)
    edit(c, mid, [photo_rows(a, mid)[0][0]], **source)
    assert photo_rows(a, mid)[0][2] == 1


@pytest.mark.parametrize("source", ["restaurant", "original"])
def test_ineligible_source_never_shares_and_clears(share, source):
    a, c = share
    mid = add_meal(c)
    pid = photo_rows(a, mid)[0][0]
    edit(c, mid, [pid])
    assert photo_rows(a, mid)[0][2] == 1
    edit(c, mid, [pid], source_type=source, source_title="Somewhere", source_detail="")
    assert photo_rows(a, mid)[0][2] == 0                 # leaving a catalog source unshares
    edit(c, mid)                                          # and coming back doesn't re-share
    assert photo_rows(a, mid)[0][2] == 0


def test_share_eligible_rule(share):
    a, _ = share
    assert a.share_eligible("cookbook", None, None) and a.share_eligible("online", None, None)
    assert a.share_eligible("original", None, "b" * 32) and a.share_eligible("original", "b" * 32, None)
    assert not a.share_eligible("original", None, None) and not a.share_eligible("restaurant", None, None)
    assert not a.share_eligible("original", "not-a-uid", None)


def test_form_offers_toggle_with_plain_wording(share):
    a, c = share
    mid = add_meal(c)
    page = c.get(f"/meal/{mid}/edit").get_data(as_text=True)
    assert 'name="share_photo"' in page and "Show on the cookbook page" in page
    assert "anyone can see" in page
    assert 'class="share" data-show="cookbook online">' in page          # shown
    rid = add_meal(c, source_type="restaurant", source_title="Luigi", source_detail="")
    page = c.get(f"/meal/{rid}/edit").get_data(as_text=True)
    assert 'class="share" data-show="cookbook online" hidden>' in page   # there for a source switch, hidden
    assert "checked" not in page.split('name="share_photo"')[1].split(">")[0]


def test_detail_marks_shared_photos(share):
    a, c = share
    mid = add_meal(c)
    assert "On the cookbook page" not in c.get(f"/meal/{mid}").get_data(as_text=True)
    edit(c, mid, [photo_rows(a, mid)[0][0]])
    assert "On the cookbook page" in c.get(f"/meal/{mid}").get_data(as_text=True)
