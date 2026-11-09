"""Photos are family photos behind an access proxy: browsers may keep them, shared caches may not."""
import os

from test_app import load_app
from test_photos import jpeg_with


def test_photos_are_cached_privately_and_immutably(tmp_path):
    c = load_app(tmp_path).app.test_client()
    c.post("/new", data={"name": "Pie", "photos": (jpeg_with(), "a.jpg")}, content_type="multipart/form-data")
    names = os.listdir(tmp_path / "photos")
    assert len(names) == 2                            # the photo and its thumbnail
    for n in names:
        resp = c.get(f"/photos/{n}")
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "private, max-age=31536000, immutable"
        assert "public" not in resp.headers["Cache-Control"]


def test_missing_photo_is_a_404_without_the_long_cache(tmp_path):
    resp = load_app(tmp_path).app.test_client().get("/photos/nope.jpg")
    assert resp.status_code == 404
    assert "immutable" not in resp.headers.get("Cache-Control", "")
