"""Links from Dinner Log into Cookbook Collection: by stable uid when a meal or pick has one, by name otherwise.

Cookbook Collection's `#/uid/<book>[/<recipe>]` route resolves the uid. Recipe websites (kind 'online') are
editor-only there, so their links go through /admin.
"""
import pytest

from test_app import load_app, text
from test_suggest import catalog_reply, recipe

CB = "https://cookbooks.example.com"
BU = "b" * 32
RU = "a1" * 16


@pytest.fixture()
def a(tmp_path, monkeypatch):
    mod = load_app(tmp_path)
    monkeypatch.setattr(mod, "COOKBOOK_URL", CB)
    return mod


def add_meal(mod, day="2026-10-28", name="Roast Chicken", source_type="cookbook", title="The Zuni Cafe Cookbook",
             detail="342", book_uid=None, recipe_uid=None):
    with mod.app.app_context():
        cur = mod.db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, source_detail, cookbook_book_uid, "
            "cookbook_recipe_uid) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day, name, source_type, title, detail, book_uid, recipe_uid))
        mod.db().commit()
        return cur.lastrowid


def detail(mod, meal_id):
    return text(mod.app.test_client().get(f"/meal/{meal_id}"))


def test_uid_url_for_a_cookbook_and_a_recipe(a):
    assert a.cookbook_uid_url(BU) == f"{CB}/#/uid/{BU}"
    assert a.cookbook_uid_url(BU, RU) == f"{CB}/#/uid/{BU}/{RU}"


def test_uid_url_for_a_recipe_website_goes_through_admin(a):
    assert a.cookbook_uid_url(BU, RU, online=True) == f"{CB}/admin#/uid/{BU}/{RU}"


@pytest.mark.parametrize("bad", [None, "", "not-a-uid", "../" * 11, "z" * 32, "b" * 31, "b" * 33, f"{BU}/{RU}"])
def test_uid_url_refuses_anything_but_32_hex(a, bad):
    assert a.cookbook_uid_url(bad) == ""
    assert a.cookbook_uid_url(BU, bad) == f"{CB}/#/uid/{BU}"  # a bad recipe uid just drops to the book


def test_uid_url_needs_a_catalog_address(a, monkeypatch):
    monkeypatch.setattr(a, "COOKBOOK_URL", "")
    assert a.cookbook_uid_url(BU, RU) == ""


def test_catalog_url_levels(a):
    both = {"cookbook_book_uid": BU, "cookbook_recipe_uid": RU, "source_type": "cookbook"}
    assert a.catalog_url(both) == f"{CB}/#/uid/{BU}"
    assert a.catalog_url(both, recipe=True) == f"{CB}/#/uid/{BU}/{RU}"
    book_only = {"cookbook_book_uid": BU, "cookbook_recipe_uid": None, "source_type": "cookbook"}
    assert a.catalog_url(book_only) == f"{CB}/#/uid/{BU}"
    assert a.catalog_url(book_only, recipe=True) == ""          # no recipe uid: the caller falls back to a search
    assert a.catalog_url({"source_type": "cookbook"}) == ""     # a pick with no uid keys at all
    assert a.catalog_url({**both, "source_type": "online"}, recipe=True) == f"{CB}/admin#/uid/{BU}/{RU}"


def test_detail_links_a_cookbook_meal_by_uid(a):
    page = detail(a, add_meal(a, book_uid=BU, recipe_uid=RU))
    assert f'href="{CB}/#/uid/{BU}/{RU}"' in page and "Open recipe in Cookbook Collection" in page
    assert f'href="{CB}/#/uid/{BU}"' in page            # the title links to the book
    assert "Search recipe in Cookbook Collection" not in page and "#/?q=" not in page


def test_detail_links_a_recipe_website_through_admin(a):
    page = detail(a, add_meal(a, source_type="online", title="Recipe Site", detail="https://site.example.com/r",
                              book_uid=BU, recipe_uid=RU))
    assert f'href="{CB}/admin#/uid/{BU}/{RU}"' in page
    assert "Open recipe in Cookbook Collection (editor)" in page
    assert f'href="{CB}/admin#/uid/{BU}"' in page


def test_detail_without_uids_keeps_the_name_search(a):
    page = detail(a, add_meal(a))
    assert f"{CB}/#/?q=Roast+Chicken" in page and "Search recipe in Cookbook Collection" in page
    assert f"{CB}/#/?q=The+Zuni+Cafe+Cookbook" in page  # the title, too
    assert "#/uid/" not in page


def test_detail_with_only_a_book_uid_links_the_book_and_searches_the_recipe(a):
    page = detail(a, add_meal(a, book_uid=BU))
    assert f'href="{CB}/#/uid/{BU}"' in page
    assert f"{CB}/#/?q=Roast+Chicken" in page and "Search recipe in Cookbook Collection" in page
    assert "Open recipe in Cookbook Collection" not in page


def test_an_online_meal_without_uids_gets_no_catalog_link(a):
    page = detail(a, add_meal(a, source_type="online", title="Recipe Site", detail="https://site.example.com/r"))
    assert CB not in page


def test_no_catalog_address_means_no_catalog_links_even_with_uids(a, monkeypatch):
    mid = add_meal(a, book_uid=BU, recipe_uid=RU)
    monkeypatch.setattr(a, "COOKBOOK_URL", "")
    page = detail(a, mid)
    assert "#/uid/" not in page and "Cookbook Collection" not in page


def pick_page(a, monkeypatch, **r):
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: catalog_reply(
        {**recipe("Zuni Roast Chicken", "The Zuni Cafe Cookbook", "342"), **r}))
    return text(a.app.test_client().get("/?suggest=1&mode=new"))


def test_pick_card_links_a_catalog_recipe_by_uid(a, monkeypatch):
    page = pick_page(a, monkeypatch, id=53, uid=RU, book_id=57, book_uid=BU)
    assert f'href="{CB}/#/uid/{BU}/{RU}"' in page and "View in Cookbook Collection ↗" in page
    assert "#/?q=" not in page


def test_pick_card_links_a_recipe_website_through_admin(a, monkeypatch):
    page = pick_page(a, monkeypatch, id=53, uid=RU, book_id=57, book_uid=BU, online=True,
                     url="https://site.example.com/r")
    assert f'href="{CB}/admin#/uid/{BU}/{RU}"' in page and "View in Cookbook Collection (editor) ↗" in page


def test_pick_card_from_an_older_catalog_keeps_the_name_search(a, monkeypatch):
    page = pick_page(a, monkeypatch, id=53, book_id=57)        # no uids in the reply
    assert f"{CB}/#/?q=Zuni+Roast+Chicken" in page and "#/uid/" not in page


def test_pick_card_ignores_a_malformed_uid_from_the_catalog(a, monkeypatch):
    page = pick_page(a, monkeypatch, id=53, uid="x", book_id=57, book_uid='"><script>')
    assert '"><script>' not in page and "#/uid/" not in page


def test_detail_offers_another_dish_for_the_same_day(a):
    page = detail(a, add_meal(a, day="2026-10-28"))
    assert 'href="/new?day=2026-10-28"' in page and "+ Another dish for this dinner" in page


def test_another_dish_form_opens_on_that_day(a):
    c = a.app.test_client()
    page = text(c.get("/new?day=2026-10-28"))
    assert 'value="2026-10-28"' in page
    c.post("/new", data={"name": "Side salad", "day": "2026-10-28"})
    with a.app.app_context():
        days = [r["day"] for r in a.db().execute("SELECT day FROM meals WHERE name='Side salad'")]
    assert days == ["2026-10-28"]
