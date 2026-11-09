import io
import os

import pytest
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


@pytest.fixture(params=["with_heif", "without_heif"])
def heif_mode(request, monkeypatch):
    """Run a test with the HEIF decoder registered and again with it removed, whatever is installed."""
    if request.param == "with_heif":
        pytest.importorskip("pillow_heif")
        return True
    # load_app() re-imports the app, which re-registers pillow_heif, so tests unregister after it
    return False


def app_for(tmp_path, heif, monkeypatch):
    a = load_app(tmp_path)
    if not heif:
        monkeypatch.delitem(Image.OPEN, "HEIF", raising=False)
    return a


def test_unreadable_photo_is_reported_not_silently_dropped(tmp_path, heif_mode, monkeypatch):
    # A file that claims to be HEIC but isn't decodable is skipped and counted with or without pillow-heif.
    c = app_for(tmp_path, heif_mode, monkeypatch).app.test_client()
    heic = io.BytesIO(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
    resp = c.post("/new", data={"name": "Tacos", "photos": [(heic, "IMG_0001.heic"), (jpeg_with(), "ok.jpg")]},
                  follow_redirects=True)
    html = text(resp)
    assert "1 photo couldn" in html and "skipped" in html
    assert len(saved_photos(tmp_path)) == 1          # the good one still saved


def test_skipped_notice_names_what_is_supported(tmp_path, heif_mode, monkeypatch):
    c = app_for(tmp_path, heif_mode, monkeypatch).app.test_client()
    html = text(c.post("/new", data={"name": "Tacos", "photos": (io.BytesIO(b"nope"), "x.bin")},
                       follow_redirects=True))
    assert ("JPEG, PNG, WebP and HEIC" in html) is heif_mode
    assert ("JPEG, PNG and WebP" in html) is (not heif_mode)


def heic_bytes(size):
    """A real HEIC file, written without registering the opener (the app must do that itself)."""
    pillow_heif = pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", size, "green")).save(buf)
    buf.seek(0)
    return buf


def test_real_heic_is_accepted_when_pillow_heif_is_installed(tmp_path, monkeypatch):
    buf = heic_bytes((640, 480))
    monkeypatch.delitem(Image.OPEN, "HEIF", raising=False)   # so importing the app has to register it
    a = load_app(tmp_path)
    assert "HEIF" in Image.OPEN
    resp = a.app.test_client().post("/new", data={"name": "Phone", "photos": (buf, "IMG_0002.HEIC")},
                                    follow_redirects=True)
    assert "skipped" not in text(resp)
    (img,) = saved_photos(tmp_path)
    assert img.format == "JPEG" and img.size == (640, 480)


def test_real_heic_is_refused_without_the_plugin(tmp_path, monkeypatch):
    buf = heic_bytes((64, 48))
    a = app_for(tmp_path, False, monkeypatch)
    resp = a.app.test_client().post("/new", data={"name": "Phone", "photos": (buf, "IMG_0002.HEIC")},
                                    follow_redirects=True)
    assert "1 photo couldn" in text(resp)
    assert saved_photos(tmp_path) == []


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


# ---- decoder allow-list: JPEG, PNG, WebP and (when pillow-heif is installed) HEIC only

def image_bytes(fmt, size=(64, 48), **kw):
    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, fmt, **kw)
    buf.seek(0)
    return buf


def upload(tmp_path, buf, filename):
    a = load_app(tmp_path)
    resp = a.app.test_client().post("/new", data={"name": "X", "photos": (buf, filename)}, follow_redirects=True)
    return text(resp)


def test_png_and_webp_are_accepted(tmp_path):
    for fmt, name in (("PNG", "a.png"), ("WEBP", "a.webp")):
        d = tmp_path / fmt
        d.mkdir()
        assert "skipped" not in upload(d, image_bytes(fmt), name)
        assert len(saved_photos(d)) == 1


def test_bmp_and_tiff_are_refused_not_decoded(tmp_path):
    for fmt, name in (("BMP", "a.bmp"), ("TIFF", "a.tif"), ("GIF", "a.gif")):
        d = tmp_path / fmt
        d.mkdir()
        assert "1 photo couldn" in upload(d, image_bytes(fmt), name)
        assert saved_photos(d) == []


def test_open_image_only_offers_the_allow_list(tmp_path):
    a = load_app(tmp_path)
    assert set(a.image_formats()) <= {"JPEG", "PNG", "WEBP", "HEIF"}
    assert {"JPEG", "PNG"} <= set(a.image_formats())
    with pytest.raises(ValueError):
        a.open_image(image_bytes("BMP"))


def test_mpo_jpeg_gets_the_draft_decode(tmp_path, monkeypatch):
    # Many Android cameras write JPEGs with a multi-picture index; Pillow reports those as MPO, which
    # must still be draft-decoded or a 50 MP photo is decoded at full size.
    from PIL import JpegImagePlugin
    first = Image.new("RGB", (4000, 3000), "red")
    buf = io.BytesIO()
    first.save(buf, "MPO", save_all=True, append_images=[Image.new("RGB", (4000, 3000), "green")])
    buf.seek(0)
    assert Image.open(buf).format == "MPO"
    buf.seek(0)
    calls = []
    real = JpegImagePlugin.JpegImageFile.draft
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft",
                        lambda self, *a, **k: calls.append(a) or real(self, *a, **k))
    assert "skipped" not in upload(tmp_path, buf, "mpo.jpg")
    assert calls, "draft() was not called for an MPO image"
    assert len(saved_photos(tmp_path)) == 1
