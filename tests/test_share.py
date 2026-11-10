"""Sharing into the app from Android: the /share fallback route and the share -> meal mapping.

The service worker normally answers the share POST itself (static/sw.js); these cover the server side,
which runs when the worker isn't controlling the app yet.
"""
import io
import os

import pytest
from PIL import Image

from test_app import jpeg, load_app, text


@pytest.fixture()
def a(tmp_path):
    return load_app(tmp_path)


@pytest.fixture()
def c(a):
    return a.app.test_client()


def photos_on_disk(tmp_path):
    return sorted(n for n in os.listdir(tmp_path / "photos") if not n.endswith("-t.jpg"))


def meal_count(a):
    with a.app.app_context():
        return a.db().execute("SELECT COUNT(*) FROM meals").fetchone()[0]


# ------------------------------------------------------------------ share_prefill
@pytest.mark.parametrize("title, txt, url, expected", [
    # Chrome on Android: url empty, the address inside text
    ("Best Lasagna Recipe", "Best Lasagna Recipe https://www.example.com/lasagna?x=1", "",
     {"name": "Best Lasagna Recipe", "notes": "", "source_type": "online",
      "source_detail": "https://www.example.com/lasagna?x=1", "source_title": "example.com"}),
    # a proper url param wins over text, and the rest of text becomes notes
    ("Soup", "Try this one", "https://recipes.example.org/soup",
     {"name": "Soup", "notes": "Try this one", "source_type": "online",
      "source_detail": "https://recipes.example.org/soup", "source_title": "recipes.example.org"}),
    # trailing punctuation from prose isn't part of the link
    ("", "Look at https://example.com/a-b.", "",
     {"name": "", "notes": "Look at", "source_type": "online",
      "source_detail": "https://example.com/a-b", "source_title": "example.com"}),
    # no link: nothing is guessed about the source
    ("", "  leftover   rice  ", "", {"name": "", "notes": "leftover rice"}),
    ("", "", "", {"name": "", "notes": ""}),
])
def test_share_prefill(a, title, txt, url, expected):
    assert a.share_prefill(title, txt, url) == expected


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x",
                                 "https://", "not a url", "https://example.com/" + "a" * 3000])
def test_share_prefill_ignores_non_web_links(a, url):
    assert "source_detail" not in a.share_prefill("", url, url)


def test_share_prefill_caps_lengths(a):
    out = a.share_prefill("t" * 1000, "n" * 10000, "")
    assert len(out["name"]) == a.SHARE_MAX_NAME and len(out["notes"]) == a.SHARE_MAX_TEXT


def test_share_prefill_drops_text_that_only_repeats_the_title(a):
    assert a.share_prefill("Pho", "Pho", "")["notes"] == ""


# ------------------------------------------------------------------ POST /share (fallback)
def test_shared_photo_becomes_a_dinner_and_opens_its_edit_page(a, c, tmp_path):
    r = c.post("/share", data={"title": "", "text": "", "url": "", "photos": [(jpeg(), "IMG_1.jpg")]},
               content_type="multipart/form-data", headers={"Sec-Fetch-Site": "none"})
    assert r.status_code == 303
    assert "/edit" in r.headers["Location"] and "shared=1" in r.headers["Location"]
    assert meal_count(a) == 1
    [name] = photos_on_disk(tmp_path)
    assert Image.open(tmp_path / "photos" / name).size == (1600, 1200)   # the normal upload pipeline
    page = text(c.get(r.headers["Location"]))
    assert "Saved your shared photo" in page and "Untitled dinner" in page


def test_shared_photo_with_link_is_an_online_dinner(a, c):
    r = c.post("/share", data={"title": "Lasagna", "text": "https://example.com/lasagna",
                               "photos": [(jpeg((100, 80)), "a.jpg")]},
               content_type="multipart/form-data")
    assert r.status_code == 303
    with a.app.app_context():
        m = a.db().execute("SELECT * FROM meals").fetchone()
    assert (m["name"], m["source_type"], m["source_title"], m["source_detail"]) == \
        ("Lasagna", "online", "example.com", "https://example.com/lasagna")


def test_shared_photo_without_link_is_our_own(a, c):
    c.post("/share", data={"photos": [(jpeg((100, 80)), "a.jpg")]}, content_type="multipart/form-data")
    with a.app.app_context():
        assert a.db().execute("SELECT source_type FROM meals").fetchone()[0] == "original"


def test_some_unreadable_photos_are_reported_on_the_edit_page(a, c, tmp_path):
    r = c.post("/share", data={"photos": [(jpeg((100, 80)), "a.jpg"), (io.BytesIO(b"nope"), "b.heic")]},
               content_type="multipart/form-data")
    assert "skipped=1" in r.headers["Location"]
    assert "1 photo couldn’t be read" in text(c.get(r.headers["Location"]))
    assert len(photos_on_disk(tmp_path)) == 1


def test_all_unreadable_photos_leave_no_empty_dinner(a, c, tmp_path):
    r = c.post("/share", data={"title": "Tacos", "photos": [(io.BytesIO(b"nope"), "b.heic")]},
               content_type="multipart/form-data")
    assert r.status_code == 200
    page = text(r)
    assert "couldn&#39;t be read" in page or "couldn't be read" in page
    assert 'value="Tacos"' in page                       # what was shared is still on screen
    assert meal_count(a) == 0 and photos_on_disk(tmp_path) == []


def test_shared_link_alone_prefills_the_form_without_saving(a, c):
    r = c.post("/share", data={"title": "Best Lasagna", "text": "https://www.example.com/lasagna"},
               content_type="multipart/form-data")
    assert r.status_code == 200 and meal_count(a) == 0
    page = text(r)
    assert 'value="Best Lasagna"' in page
    assert 'value="https://www.example.com/lasagna"' in page and 'value="example.com"' in page
    assert 'value="online" checked' in page
    assert 'action="/new"' in page                       # saving goes to /new, not back to /share


def test_form_posts_to_its_own_route(c):
    assert 'action="/new"' in text(c.get("/new"))
    r = c.post("/new", data={"name": "Tacos"})
    assert 'action="/meal/1/edit"' in text(c.get("/meal/1/edit"))
    assert r.status_code == 302


def test_share_is_post_only(c):
    assert c.get("/share").status_code == 405


def test_cross_site_share_is_refused(a, c):
    r = c.post("/share", data={"photos": [(jpeg((100, 80)), "a.jpg")]}, content_type="multipart/form-data",
               headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and meal_count(a) == 0
