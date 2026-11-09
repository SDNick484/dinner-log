"""480 px thumbnails written beside each photo at upload."""
import os
import re

import pytest
from PIL import Image

from test_app import load_app, text
from test_photos import jpeg_with


def files(tmp_path):
    return sorted(os.listdir(tmp_path / "photos"))


def post_meal(c, buf, name="Pie"):
    return c.post("/new", data={"name": name, "photos": (buf, "a.jpg")}, content_type="multipart/form-data")


def test_thumb_name_follows_cookbook_collection(tmp_path):
    a = load_app(tmp_path)
    assert a.thumb_name("0123abcd.jpg") == "0123abcd-t.jpg"
    assert a.thumb_name("x.jpg") == "x-t.jpg"


def test_upload_writes_full_image_and_480px_thumb(tmp_path):
    c = load_app(tmp_path).app.test_client()
    post_meal(c, jpeg_with((4000, 3000)))
    full, thumb = [n for n in files(tmp_path) if not n.endswith("-t.jpg")], [n for n in files(tmp_path) if n.endswith("-t.jpg")]
    assert len(full) == 1 and thumb == [full[0][:-4] + "-t.jpg"]
    assert Image.open(tmp_path / "photos" / full[0]).size == (1600, 1200)
    t = Image.open(tmp_path / "photos" / thumb[0])
    assert t.format == "JPEG" and t.size == (480, 360)


def test_thumb_follows_exif_orientation(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6                                 # rotate 90 degrees to display
    c = load_app(tmp_path).app.test_client()
    post_meal(c, jpeg_with((4000, 3000), exif))
    (t,) = [Image.open(tmp_path / "photos" / n) for n in files(tmp_path) if n.endswith("-t.jpg")]
    assert t.size == (360, 480)


def test_small_photo_is_not_upscaled(tmp_path):
    c = load_app(tmp_path).app.test_client()
    post_meal(c, jpeg_with((300, 200)))
    (t,) = [Image.open(tmp_path / "photos" / n) for n in files(tmp_path) if n.endswith("-t.jpg")]
    assert t.size == (300, 200)


def test_thumb_carries_no_exif_or_location(tmp_path):
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (37.0, 58.0, 0.0)}    # GPSInfo IFD
    exif[0x010F] = "PhoneMaker"
    c = load_app(tmp_path).app.test_client()
    post_meal(c, jpeg_with(exif=exif))
    (t,) = [Image.open(tmp_path / "photos" / n) for n in files(tmp_path) if n.endswith("-t.jpg")]
    assert len(t.getexif()) == 0 and "icc_profile" not in t.info


def test_no_temp_files_are_left_behind(tmp_path):
    c = load_app(tmp_path).app.test_client()
    post_meal(c, jpeg_with())
    assert not [n for n in files(tmp_path) if n.endswith(".tmp")]


def test_removing_a_photo_deletes_both_files(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    loc = post_meal(c, jpeg_with()).headers["Location"]
    assert len(files(tmp_path)) == 2
    c.post(loc + "/edit", data={"name": "Pie", "delete_photo": "1"}, content_type="multipart/form-data")
    assert files(tmp_path) == []


def test_deleting_a_meal_deletes_both_files(tmp_path):
    c = load_app(tmp_path).app.test_client()
    loc = post_meal(c, jpeg_with()).headers["Location"]
    c.post(loc + "/delete")
    assert files(tmp_path) == []


def test_remove_photo_files_tolerates_a_missing_thumb(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    post_meal(c, jpeg_with())
    (thumb,) = [n for n in files(tmp_path) if n.endswith("-t.jpg")]
    os.remove(tmp_path / "photos" / thumb)
    a.remove_photo_files([n for n in files(tmp_path)])      # one real file, one name whose thumb is gone
    assert files(tmp_path) == []


def img_srcs(html):
    return re.findall(r'<img src="/photos/([^"]+)"', html)


def test_feed_uses_thumb_but_detail_gallery_keeps_the_full_image(tmp_path):
    c = load_app(tmp_path).app.test_client()
    loc = post_meal(c, jpeg_with()).headers["Location"]
    (full,) = [n for n in files(tmp_path) if not n.endswith("-t.jpg")]
    assert img_srcs(text(c.get("/"))) == [full[:-4] + "-t.jpg"]
    assert img_srcs(text(c.get(loc))) == [full]
    assert img_srcs(text(c.get(loc + "/edit"))) == [full[:-4] + "-t.jpg"]


def test_missing_thumb_falls_back_to_the_full_image(tmp_path):
    c = load_app(tmp_path).app.test_client()
    loc = post_meal(c, jpeg_with()).headers["Location"]
    (full,) = [n for n in files(tmp_path) if not n.endswith("-t.jpg")]
    os.remove(tmp_path / "photos" / (full[:-4] + "-t.jpg"))
    assert img_srcs(text(c.get("/"))) == [full]
    assert img_srcs(text(c.get(loc + "/edit"))) == [full]


def test_thumb_write_failure_does_not_lose_the_photo(tmp_path, monkeypatch):
    a = load_app(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(a, "write_thumb", boom)
    c = a.app.test_client()
    resp = c.post("/new", data={"name": "Pie", "photos": (jpeg_with(), "a.jpg")}, follow_redirects=True)
    assert "skipped" not in text(resp)
    assert len(files(tmp_path)) == 1
