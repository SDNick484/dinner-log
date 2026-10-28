import io
import os

from PIL import Image

from test_app import load_app, text


def jpeg_with(size=(4000, 3000), exif=None):
    buf = io.BytesIO()
    im = Image.new("RGB", size, "red")
    im.save(buf, "JPEG", exif=exif) if exif is not None else im.save(buf, "JPEG")
    buf.seek(0)
    return buf


def saved_photos(tmp_path):
    d = tmp_path / "photos"
    return [Image.open(d / n) for n in sorted(os.listdir(d))]


def test_unreadable_photo_is_reported_not_silently_dropped(tmp_path):
    c = load_app(tmp_path).app.test_client()
    heic = io.BytesIO(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
    resp = c.post("/new", data={"name": "Tacos", "photos": [(heic, "IMG_0001.heic"), (jpeg_with(), "ok.jpg")]},
                  follow_redirects=True)
    html = text(resp)
    assert "1 photo couldn" in html and "skipped" in html
    assert len(saved_photos(tmp_path)) == 1          # the good one still saved


def test_no_notice_when_all_photos_saved(tmp_path):
    c = load_app(tmp_path).app.test_client()
    resp = c.post("/new", data={"name": "Tacos", "photos": (jpeg_with(), "ok.jpg")}, follow_redirects=True)
    assert "skipped" not in text(resp)


def test_oversized_image_is_rejected_before_decoding(tmp_path):
    a = load_app(tmp_path)
    huge = io.BytesIO()
    Image.new("1", (10000, 8000)).save(huge, "PNG")  # 80 MP, tiny on disk, PNG can't be draft-decoded
    huge.seek(0)
    resp = a.app.test_client().post("/new", data={"name": "Bomb", "photos": (huge, "x.png")},
                                    follow_redirects=True)
    assert "1 photo couldn" in text(resp)
    assert saved_photos(tmp_path) == []


def test_large_jpeg_is_downscaled_and_keeps_orientation(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6                                 # "rotate 90° CW to display"
    c = load_app(tmp_path).app.test_client()
    c.post("/new", data={"name": "Portrait", "photos": (jpeg_with((4000, 3000), exif), "p.jpg")})
    (img,) = saved_photos(tmp_path)
    assert img.size == (1200, 1600)


def test_location_metadata_is_stripped(tmp_path):
    # Privacy property: phone photos carry GPS; nothing from EXIF should survive.
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (37.0, 58.0, 0.0)}    # GPSInfo IFD
    exif[0x010F] = "PhoneMaker"                       # Make
    c = load_app(tmp_path).app.test_client()
    c.post("/new", data={"name": "Gps", "photos": (jpeg_with(exif=exif), "g.jpg")})
    (img,) = saved_photos(tmp_path)
    assert len(img.getexif()) == 0


def test_removed_photo_files_are_deleted_only_after_commit(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    c = a.app.test_client()
    c.post("/new", data={"name": "Pie", "photos": (jpeg_with(), "a.jpg")})
    with a.app.app_context():
        pid = a.db().execute("SELECT id FROM photos").fetchone()[0]

    states = []
    real = a.remove_photo_files

    def spy(names):
        states.append(a.db().in_transaction)
        real(names)

    monkeypatch.setattr(a, "remove_photo_files", spy)
    c.post("/meal/1/edit", data={"name": "Pie", "delete_photo": str(pid)})
    assert states == [False]                          # DB change committed before files go
    assert saved_photos(tmp_path) == []
