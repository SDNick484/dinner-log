"""backfill_thumbs: writing the missing 480 px thumbnails for photos logged before thumbnails existed."""
import importlib
import io
import os
import subprocess
import sys

import pytest
from PIL import Image

from test_app import ROOT, load_app
from test_photos import jpeg_with


@pytest.fixture()
def bt(tmp_path):
    a = load_app(tmp_path)
    sys.modules.pop("backfill_thumbs", None)
    mod = importlib.import_module("backfill_thumbs")
    c = a.app.test_client()
    names = []
    for i in range(3):
        c.post("/new", data={"name": f"Meal {i}", "photos": (jpeg_with((4000, 3000)), "a.jpg")},
               content_type="multipart/form-data")
    names = sorted(n for n in os.listdir(tmp_path / "photos") if not n.endswith("-t.jpg"))
    assert len(names) == 3
    for n in names:                                   # make them look like photos from before thumbnails
        os.remove(tmp_path / "photos" / (n[:-4] + "-t.jpg"))
    return type("BT", (), {"app": a, "mod": mod, "dir": tmp_path / "photos", "names": names})


def run(bt, *argv):
    out = io.StringIO()
    code = bt.mod.main(list(argv), out=out)
    return code, out.getvalue()


def thumbs(bt):
    return sorted(n for n in os.listdir(bt.dir) if n.endswith("-t.jpg"))


def test_dry_run_reports_and_writes_nothing(bt):
    code, out = run(bt, "--dry-run")
    assert code == 0 and thumbs(bt) == []
    assert out.count("would write a thumbnail") == 3
    assert "3 photos checked" in out and "dry run" in out
    for n in bt.names:
        assert n in out                               # one line per file


def test_real_run_writes_480px_thumbs(bt):
    code, out = run(bt)
    assert code == 0
    assert thumbs(bt) == sorted(n[:-4] + "-t.jpg" for n in bt.names)
    for t in thumbs(bt):
        im = Image.open(bt.dir / t)
        assert im.format == "JPEG" and im.size == (480, 360) and len(im.getexif()) == 0
    assert "3 photos checked: wrote 3 thumbnails, 0 already had one, 0 errors" in out
    assert not [n for n in os.listdir(bt.dir) if n.endswith(".tmp")]


def test_second_run_changes_nothing(bt):
    run(bt)
    before = {t: (bt.dir / t).stat().st_mtime_ns for t in thumbs(bt)}
    code, out = run(bt)
    assert code == 0
    assert {t: (bt.dir / t).stat().st_mtime_ns for t in thumbs(bt)} == before
    assert "wrote 0 thumbnails, 3 already had one" in out


def test_only_missing_thumbs_are_written(bt):
    run(bt)
    gone = bt.names[1][:-4] + "-t.jpg"
    os.remove(bt.dir / gone)
    code, out = run(bt)
    assert code == 0 and gone in thumbs(bt)
    assert "wrote 1 thumbnails, 2 already had one" in out


def test_missing_source_file_is_an_error_and_the_rest_still_run(bt):
    os.remove(bt.dir / bt.names[0])
    code, out = run(bt)
    assert code == 1
    assert "missing" in out and bt.names[0] in out
    assert len(thumbs(bt)) == 2                       # the other two were still done
    assert "1 errors" in out


def test_unreadable_source_is_an_error(bt):
    (bt.dir / bt.names[2]).write_bytes(b"not an image")
    code, out = run(bt)
    assert code == 1 and "error" in out
    assert len(thumbs(bt)) == 2


def test_refused_format_on_disk_is_an_error(bt):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48)).save(buf, "BMP")
    (bt.dir / bt.names[0]).write_bytes(buf.getvalue())
    code, out = run(bt)
    assert code == 1 and len(thumbs(bt)) == 2


def test_database_filename_cannot_escape_the_photo_dir(bt, tmp_path):
    with bt.app.app.app_context():
        con = bt.app.db()
        con.execute("INSERT INTO photos (meal_id, filename) VALUES (1, '../escape.jpg')")
        con.commit()
    Image.new("RGB", (64, 48)).save(tmp_path / "escape.jpg")
    code, out = run(bt)
    assert code == 1 and "unsafe" in out
    assert not (tmp_path / "escape-t.jpg").exists()


def test_runs_as_a_module(bt, tmp_path):
    r = subprocess.run([sys.executable, "-m", "backfill_thumbs", "--dry-run"], cwd=ROOT,
                       env={**os.environ, "DATA_DIR": str(tmp_path)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "would write" in r.stdout and thumbs(bt) == []
