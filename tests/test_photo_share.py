"""Sharing a dinner photo with Cookbook Collection: the `shared` flag, the edit-form toggle, the `photos` field
in /api/history and the token-only GET /api/photos/<file> that serves shared files and nothing else."""
import os
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


# ------------------------------------------------------------------ /api/history

def test_history_lists_shared_photos_only(share):
    a, c = share
    mid = add_meal(c, 3)
    rows = photo_rows(a, mid)
    meal = history(c).get_json()["meals"][0]
    assert meal["photos"] == []                           # always present, empty until something is shared
    edit(c, mid, [rows[2][0], rows[0][0]])
    body = history(c).get_json()
    assert body["version"] == 1
    assert body["meals"][0]["photos"] == [rows[0][1], rows[2][1]]   # ordered by photo id
    assert all(not f.endswith("-t.jpg") for f in body["meals"][0]["photos"])


def test_history_hides_shared_photos_of_ineligible_meals(share):
    a, c = share
    mid = add_meal(c, source_type="online", source_title="Blog", source_detail="https://blog.example.com/x")
    con = sqlite3.connect(a.DB_PATH)                      # flag set, then the meal changed outside the form
    con.execute("UPDATE photos SET shared=1")
    con.execute("UPDATE meals SET source_type='restaurant'")
    con.commit()
    con.close()
    meals = history(c).get_json()["meals"]
    assert [m["id"] for m in meals] == [mid]             # still sent: its source holds a link
    assert meals[0]["photos"] == []


def test_history_etag_changes_when_sharing_changes(share):
    a, c = share
    mid = add_meal(c)
    etag = history(c).headers["ETag"]
    assert history(c, etag).status_code == 304
    edit(c, mid, [photo_rows(a, mid)[0][0]])
    shared = history(c, etag)
    assert shared.status_code == 200 and shared.headers["ETag"] != etag
    edit(c, mid)
    assert history(c, shared.headers["ETag"]).status_code == 200


# ------------------------------------------------------------------ /api/photos/<file>

def test_photo_route_serves_shared_files_only(share, tmp_path):
    a, c = share
    mid = add_meal(c, 2)
    (p1, f1, _), (_, f2, _) = photo_rows(a, mid)
    edit(c, mid, [p1])
    r = fetch(c, f1)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert r.get_data() == (tmp_path / "photos" / f1).read_bytes()
    assert r.headers["Cache-Control"] == "private, no-store"
    assert fetch(c, f2).status_code == 404               # private: same answer as a missing file
    edit(c, mid)
    assert fetch(c, f1).status_code == 404               # unshared


def test_photo_route_needs_the_token(share):
    a, c = share
    mid = add_meal(c)
    pid, f, _ = photo_rows(a, mid)[0]
    edit(c, mid, [pid])
    for token in (None, "nope", TOKEN + "x"):
        r = fetch(c, f, token)
        assert r.status_code == 401 and TOKEN not in r.get_data(as_text=True)
    assert c.get(f"/api/photos/{f}", headers={"Authorization": TOKEN}).status_code == 401


def test_photo_route_off_without_history_token(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    mid = add_meal(c)
    pid, f, _ = photo_rows(a, mid)[0]
    edit(c, mid, [pid])
    assert c.get(f"/api/photos/{f}", headers={"Authorization": "Bearer "}).status_code == 404


def test_photo_route_refuses_thumbnails_and_odd_names(share, tmp_path):
    a, c = share
    mid = add_meal(c)
    pid, f, _ = photo_rows(a, mid)[0]
    edit(c, mid, [pid])
    thumb = f[:-4] + "-t.jpg"
    assert (tmp_path / "photos" / thumb).exists()
    for name in (thumb, f.upper(), f[:-4] + ".png", f + "x", "../meals.db", "..%2Fmeals.db", "meals.db", "x" * 32 + ".jpg"):
        r = fetch(c, name)
        assert r.status_code == 404, name
        assert not (300 <= r.status_code < 400)


def test_photo_route_hides_shared_photos_of_ineligible_meals(share):
    a, c = share
    mid = add_meal(c)
    pid, f, _ = photo_rows(a, mid)[0]
    edit(c, mid, [pid])
    con = sqlite3.connect(a.DB_PATH)
    con.execute("UPDATE meals SET source_type='restaurant'")   # outside the form, so the flag survives
    con.commit()
    con.close()
    assert fetch(c, f).status_code == 404


def test_photo_route_with_flag_but_missing_file(share, tmp_path):
    a, c = share
    mid = add_meal(c)
    pid, f, _ = photo_rows(a, mid)[0]
    edit(c, mid, [pid])
    os.remove(tmp_path / "photos" / f)
    assert fetch(c, f).status_code == 404


def test_photo_route_never_redirects(share):
    a, c = share
    for path in ("/api/photos/", "/api/photos", "/api/photos/abc/"):
        r = c.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
        assert not (300 <= r.status_code < 400), path
