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


def test_pwa_assets(client):
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/manifest.json").status_code == 200
    assert 'crossorigin="use-credentials"' in text(client.get("/"))
