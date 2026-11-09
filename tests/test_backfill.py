"""backfill_uids: stamping uids on meals logged with only Cookbook Collection's int ids.

The catalog is a dict standing in for GET /api/books/<id>; no HTTP is made.
"""
import importlib
import io
import sqlite3
import sys
import urllib.error

import pytest

from test_app import load_app

BOOK = "b" * 32
OTHER_BOOK = "c" * 32
CHICKEN = "1" * 32
CAESAR = "2" * 32
NEWCOMER = "3" * 32

CATALOG = {
    7: {"id": 7, "uid": BOOK, "title": "The Zuni Café Cookbook", "recipes": [
        {"id": 1, "uid": CHICKEN, "name": "Zuni Roast Chicken with Bread Salad", "heading": None, "page": "342"},
        {"id": 2, "uid": CAESAR, "name": "Caesar Salad", "heading": "Salads", "page": "120"},
        # id 3 was a deleted recipe; the rowid now belongs to a different one
        {"id": 3, "uid": NEWCOMER, "name": "Polenta", "heading": None, "page": "210"}]},
    # book 9 was "Dining In"; it was deleted and the rowid reused by another book
    9: {"id": 9, "uid": OTHER_BOOK, "title": "Salt Fat Acid Heat", "recipes": []},
}


def fetch(book_id):
    return CATALOG.get(book_id)  # None stands for a 404


@pytest.fixture()
def bf(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    sys.modules.pop("backfill_uids", None)
    mod = importlib.import_module("backfill_uids")
    assert mod.app is a
    con = sqlite3.connect(a.DB_PATH)
    meals = [  # (name, type, title, detail, book id, recipe id, book uid, recipe uid)
        ("Roast chicken", "cookbook", "zuni cafe cookbook", "342", 7, 1, None, None),        # 1 both match
        ("Salads, caesar salad", "cookbook", "The Zuni Café Cookbook", "", 7, 2, None, None),  # 2 name match via heading
        ("Braised leeks", "cookbook", "Zuni Cafe Cookbook", "88", 7, 3, None, None),          # 3 recipe id reused
        ("Chicken thighs", "cookbook", "Dining In", "54", 9, 12, None, None),                 # 4 book id reused
        ("Pho", "cookbook", "Gone Book", "10", 404, 5, None, None),                           # 5 book deleted
        ("Marbella", "online", "NYT Cooking", "https://x.example/1", None, 77, None, None),   # 6 no book id
        ("Roast chicken", "cookbook", "Zuni", "342", 7, 1, BOOK, CHICKEN),                    # 7 already done
        ("Tacos", "restaurant", "Taqueria", "", None, None, None, None),                      # 8 never considered
        ("Roast chicken", "cookbook", "Zuni Café Cookbook", "342", 7, None, None, None),      # 9 book id only
    ]
    con.executemany("INSERT INTO meals (day, name, source_type, source_title, source_detail, cookbook_book_id, "
                    "cookbook_recipe_id, cookbook_book_uid, cookbook_recipe_uid) VALUES ('2026-10-01',?,?,?,?,?,?,?,?)",
                    meals)
    con.commit()
    con.close()
    return a, mod


def state(a):
    con = sqlite3.connect(a.DB_PATH)
    try:
        return {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT id, cookbook_book_uid, cookbook_recipe_uid FROM meals ORDER BY id")}
    finally:
        con.close()


def run(mod, *argv):
    out = io.StringIO()
    code = mod.main(list(argv), fetch=fetch, out=out)
    return code, out.getvalue()


def test_dry_run_reports_and_writes_nothing(bf):
    a, mod = bf
    before = state(a)
    code, text = run(mod, "--dry-run")
    assert code == 0 and state(a) == before
    lines = {int(l.split()[1]): l for l in text.splitlines() if l.startswith("meal ")}
    assert set(lines) == {1, 2, 3, 4, 5, 6, 9}                        # 7 is done, 8 is a restaurant
    assert f"would set book_uid={BOOK}, recipe_uid={CHICKEN}  [ok]" in lines[1]
    assert f"recipe_uid={CAESAR}" in lines[2]
    assert f"would set book_uid={BOOK}  [conflict (recipe left NULL): recipe 3 is now 'Polenta'" in lines[3]
    assert "would set nothing  [conflict: book 9 is now 'Salt Fat Acid Heat'" in lines[4]
    assert "isn't in the public catalog" in lines[5]
    assert "recipe id without a book id" in lines[6]
    assert f"would set book_uid={BOOK}  [ok]" in lines[9]
    assert text.rstrip().endswith("would set 4 book uids and 2 recipe uids; 2 conflicts, 3 meals left unchanged "
                                  "(dry run: nothing written)")


def test_write_then_rerun_is_a_no_op(bf):
    a, mod = bf
    assert run(mod)[0] == 0
    s = state(a)
    assert s[1] == (BOOK, CHICKEN) and s[2] == (BOOK, CAESAR)
    assert s[3] == (BOOK, None)                                      # reused recipe id: book only
    assert s[4] == (None, None) and s[5] == (None, None) and s[6] == (None, None)
    assert s[7] == (BOOK, CHICKEN) and s[8] == (None, None) and s[9] == (BOOK, None)
    code, text = run(mod)
    assert code == 0 and state(a) == s                               # idempotent
    assert "set 0 book uids and 0 recipe uids" in text


def test_never_overwrites_a_uid_set_meanwhile(bf):
    a, mod = bf
    con = sqlite3.connect(a.DB_PATH)
    con.row_factory = sqlite3.Row
    decisions = mod.plan(con, fetch)
    con.execute("UPDATE meals SET cookbook_book_uid=? WHERE id=1", ("d" * 32,))   # the form saved one since
    con.commit()
    mod.apply(con, decisions)
    con.close()
    assert state(a)[1] == ("d" * 32, CHICKEN)


def test_catalog_down_writes_nothing(bf, capsys):
    a, mod = bf
    before = state(a)

    def down(book_id):
        raise urllib.error.URLError("connection refused")
    assert mod.main([], fetch=down, out=io.StringIO()) == 1
    assert state(a) == before
    assert "nothing was written" in capsys.readouterr().err


def test_catalog_without_uids(bf):
    a, mod = bf
    old = {7: {**CATALOG[7], "uid": None}}
    code, text = run_with(mod, old.get)
    assert code == 0 and "upgrade Cookbook Collection first" in text and state(a)[1] == (None, None)


def run_with(mod, f):
    out = io.StringIO()
    return mod.main([], fetch=f, out=out), out.getvalue()


def test_needs_a_catalog_address(bf, monkeypatch, capsys):
    a, mod = bf
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "")
    assert mod.main(["--dry-run"]) == 1
    assert "COOKBOOK_API_URL" in capsys.readouterr().err


def test_fetch_book_maps_404_to_none_and_raises_otherwise(bf, monkeypatch):
    a, mod = bf
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:8767")
    seen = []

    class Opener:
        def open(self, req, timeout=None):
            seen.append(req.full_url)
            code = 404 if req.full_url.endswith("/5") else 500
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    monkeypatch.setattr(a, "_cookbook_opener", Opener())
    assert mod.fetch_book(5) is None
    with pytest.raises(urllib.error.HTTPError):
        mod.fetch_book(6)
    assert seen == ["http://127.0.0.1:8767/api/books/5", "http://127.0.0.1:8767/api/books/6"]
