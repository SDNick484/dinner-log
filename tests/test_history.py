"""Cookbook Collection ids on meals, and the history endpoint Cookbook Collection reads."""
import sqlite3

import pytest

from test_app import load_app

OLD_SCHEMA = """
CREATE TABLE meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'original', source_title TEXT NOT NULL DEFAULT '',
    source_detail TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    rating INTEGER CHECK (rating BETWEEN 1 AND 10), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def row(a, meal_id):
    con = sqlite3.connect(a.DB_PATH)
    try:
        return con.execute("SELECT cookbook_book_id, cookbook_recipe_id FROM meals WHERE id=?",
                           (meal_id,)).fetchone()
    finally:
        con.close()


def test_upgrade_adds_id_columns(tmp_path):
    con = sqlite3.connect(tmp_path / "meals.db")
    con.executescript(OLD_SCHEMA)
    con.execute("INSERT INTO meals (day, name) VALUES ('2026-10-01', 'Old soup')")
    con.commit()
    con.close()
    a = load_app(tmp_path)
    con = sqlite3.connect(a.DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(meals)")}
    con.close()
    assert {"cookbook_book_id", "cookbook_recipe_id"} <= cols
    assert row(a, 1) == (None, None)


@pytest.mark.parametrize("raw,want", [("17", 17), (17, 17), (" 17 ", 17), ("x", None), ("0", None),
                                      ("-3", None), ("1e3", None), ("", None), (None, None),
                                      ("١٢", None)])
def test_parse_cookbook_id(tmp_path, raw, want):
    assert load_app(tmp_path).parse_cookbook_id(raw) == want


def test_ids_dropped_for_non_catalog_sources(tmp_path):
    a = load_app(tmp_path)
    for st in ("restaurant", "original"):
        v = a.parse_meal_params({"source_type": st, "cookbook_book_id": "3", "cookbook_recipe_id": "4"})
        assert (v["cookbook_book_id"], v["cookbook_recipe_id"]) == (None, None)
    for st in ("cookbook", "online"):
        v = a.parse_meal_params({"source_type": st, "cookbook_book_id": "3", "cookbook_recipe_id": "4"})
        assert (v["cookbook_book_id"], v["cookbook_recipe_id"]) == (3, 4)


def test_new_saves_ids_and_edit_keeps_them(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    form = {"name": "Roast chicken", "source_type": "cookbook", "source_title": "Zuni",
            "source_detail": "342", "cookbook_book_id": "7", "cookbook_recipe_id": "1"}
    loc = c.post("/new", data=form).headers["Location"]
    meal_id = int(loc.rstrip("/").split("/")[-1].split("?")[0])
    assert row(a, meal_id) == (7, 1)

    html = c.get(f"/meal/{meal_id}/edit").get_data(as_text=True)
    assert 'name="cookbook_book_id" id="cookbook-book-id" value="7"' in html
    assert 'name="cookbook_recipe_id" id="cookbook-recipe-id" value="1"' in html

    c.post(f"/meal/{meal_id}/edit", data=form)
    assert row(a, meal_id) == (7, 1)
    c.post(f"/meal/{meal_id}/edit", data={**form, "source_type": "restaurant"})
    assert row(a, meal_id) == (None, None)


def test_new_form_prefills_ids_from_query(tmp_path):
    c = load_app(tmp_path).app.test_client()
    html = c.get("/new?source_type=cookbook&cookbook_book_id=7&cookbook_recipe_id=1").get_data(as_text=True)
    assert 'id="cookbook-book-id" value="7"' in html and 'id="cookbook-recipe-id" value="1"' in html
