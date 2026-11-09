"""Cookbook Collection uids on meals: the migration, parsing, saving, the picker and the history feed.

Ints (cookbook_book_id / cookbook_recipe_id) are SQLite rowids on the catalog side and can be reused after a
delete; uids never are. Dinner Log stores both, and the ints keep working for catalogs older than uids.
"""
import sqlite3

import pytest

from test_app import load_app
from test_history import TOKEN, get
from test_recipe_picker import BOOKS, catalog

BU = "b" * 32
RU = "a1" * 16

PRE_UID_SCHEMA = """
CREATE TABLE meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'original', source_title TEXT NOT NULL DEFAULT '',
    source_detail TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    rating INTEGER CHECK (rating BETWEEN 1 AND 10), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cookbook_book_id INTEGER, cookbook_recipe_id INTEGER);
"""


def uids(a, meal_id):
    con = sqlite3.connect(a.DB_PATH)
    try:
        return con.execute("SELECT cookbook_book_uid, cookbook_recipe_uid FROM meals WHERE id=?",
                           (meal_id,)).fetchone()
    finally:
        con.close()


def test_upgrade_adds_uid_columns_and_keeps_rows(tmp_path):
    con = sqlite3.connect(tmp_path / "meals.db")
    con.executescript(PRE_UID_SCHEMA)
    con.execute("INSERT INTO meals (day, name, source_type, cookbook_book_id, cookbook_recipe_id) "
                "VALUES ('2026-10-01', 'Roast chicken', 'cookbook', 7, 1)")
    con.commit()
    con.close()
    a = load_app(tmp_path)
    con = sqlite3.connect(a.DB_PATH)
    cols = {r[1]: r[2] for r in con.execute("PRAGMA table_info(meals)")}
    kept = con.execute("SELECT cookbook_book_id, cookbook_recipe_id FROM meals WHERE id=1").fetchone()
    con.close()
    assert cols["cookbook_book_uid"] == "TEXT" and cols["cookbook_recipe_uid"] == "TEXT"
    assert kept == (7, 1) and uids(a, 1) == (None, None)       # existing rows: ints kept, uids NULL
    load_app(tmp_path)                                         # a second start is a no-op


@pytest.mark.parametrize("raw,want", [(BU, BU), (BU.upper(), BU), (f"  {RU} ", RU), ("", None), (None, None),
                                      ("b" * 31, None), ("b" * 33, None), ("g" * 32, None), ("7", None),
                                      ("b" * 32 + "\n", BU), ("１" * 32, None)],
                         ids=lambda v: repr(v)[:14] if isinstance(v, str) else None)
def test_parse_cookbook_uid(tmp_path, raw, want):
    assert load_app(tmp_path).parse_cookbook_uid(raw) == want


def test_new_and_edit_save_uids_and_drop_them_off_catalog(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    form = {"name": "Roast chicken", "source_type": "cookbook", "source_title": "Zuni", "source_detail": "342",
            "cookbook_book_id": "7", "cookbook_recipe_id": "1", "cookbook_book_uid": BU, "cookbook_recipe_uid": RU}
    c.post("/new", data=form)
    assert uids(a, 1) == (BU, RU)
    html = c.get("/meal/1/edit").get_data(as_text=True)
    assert f'id="cookbook-book-uid" value="{BU}"' in html and f'id="cookbook-recipe-uid" value="{RU}"' in html
    c.post("/meal/1/edit", data={**form, "cookbook_recipe_uid": "not-a-uid"})
    assert uids(a, 1) == (BU, None)                            # malformed: dropped, not stored
    c.post("/meal/1/edit", data={**form, "source_type": "restaurant"})
    assert uids(a, 1) == (None, None)


def test_new_form_prefills_uids_from_query(tmp_path):
    c = load_app(tmp_path).app.test_client()
    html = c.get(f"/new?source_type=cookbook&cookbook_book_uid={BU}&cookbook_recipe_uid={RU}").get_data(as_text=True)
    assert f'id="cookbook-book-uid" value="{BU}"' in html and f'id="cookbook-recipe-uid" value="{RU}"' in html


def test_form_clears_uids_with_ids():
    """The picker's JS forgets the uids whenever it forgets the ids (typing over the name, book or page)."""
    from test_app import ROOT
    js = open(f"{ROOT}/templates/form.html").read()
    assert "bookIdInput.value = recipeIdInput.value = bookUidInput.value = recipeUidInput.value = ''" in js
    assert "recipeUidInput.value = r.uid || ''" in js


# ------------------------------------------------------------------ the picker and "Pick for tonight"

ZUNI_UIDS = {"id": 7, "uid": BU, "title": "The Zuni Café Cookbook", "recipes": [
    {"id": 1, "uid": RU, "name": "Zuni Roast Chicken with Bread Salad", "page": "342"},
    {"id": 2, "uid": "NOT-HEX", "name": "Zuni Caesar Salad", "page": None}]}


def test_picker_returns_uids(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    catalog(monkeypatch, a, {"/api/books": BOOKS, "/api/books/7": ZUNI_UIDS})
    r = a.app.test_client().get("/cookbook/recipes?title=zuni%20cafe%20cookbook").get_json()
    assert r["book_id"] == 7 and r["book_uid"] == BU
    assert [(x["id"], x["uid"]) for x in r["recipes"]] == [(1, RU), (2, None)]
    cached = a.cookbook_book("The Zuni Café Cookbook")         # from the cache: the uid is still there
    assert cached["book_uid"] == BU


def test_catalog_dish_carries_uids(tmp_path):
    a = load_app(tmp_path)
    import datetime
    d = a.catalog_dish({"id": 1, "uid": RU, "book_id": 7, "book_uid": BU, "name": "Roast", "book_title": "Zuni"},
                       datetime.date(2026, 11, 9))
    assert (d["cookbook_book_uid"], d["cookbook_recipe_uid"]) == (BU, RU)
    d = a.catalog_dish({"id": 1, "book_id": 7, "name": "Roast"}, datetime.date(2026, 11, 9))  # older catalog
    assert (d["cookbook_book_uid"], d["cookbook_recipe_uid"]) == (None, None)


def test_log_tonight_link_carries_uids(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:8767")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: {"recipes": [
        {"id": 1, "uid": RU, "book_id": 7, "book_uid": BU, "name": "Roast", "book_title": "Zuni", "page": "342"}]})
    html = a.app.test_client().get("/?suggest=1&mode=new").get_data(as_text=True)
    assert f"cookbook_book_uid={BU}" in html and f"cookbook_recipe_uid={RU}" in html


# ------------------------------------------------------------------ GET /api/history

def test_history_sends_uids_and_includes_uid_only_rows(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "HISTORY_TOKEN", TOKEN)
    c = a.app.test_client()
    c.post("/new", data={"day": "2026-10-01", "name": "Roast chicken", "source_type": "cookbook",
                         "cookbook_book_id": "7", "cookbook_recipe_id": "1",
                         "cookbook_book_uid": BU, "cookbook_recipe_uid": RU})
    c.post("/new", data={"day": "2026-10-02", "name": "Leftovers", "source_type": "original"})
    con = sqlite3.connect(a.DB_PATH)
    con.execute("UPDATE meals SET cookbook_book_uid=? WHERE id=2", (BU,))   # uid only, no int, not a cookbook
    con.commit()
    con.close()
    body = get(c).get_json()
    assert body["version"] == 1                                # additive: still version 1
    first, second = body["meals"]
    assert (first["cookbook_book_uid"], first["cookbook_recipe_uid"]) == (BU, RU)
    assert second["name"] == "Leftovers" and second["cookbook_book_uid"] == BU
