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
