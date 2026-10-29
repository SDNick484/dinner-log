"""The form's recipe picker: recipes of an indexed cookbook, fetched from Cookbook Collection's public API."""
import io
import json
import urllib.error

from test_app import load_app

BOOKS = {"books": [{"id": 7, "title": "The Zuni Café Cookbook"}, {"id": 9, "title": "Dining In"}]}
ZUNI = {"id": 7, "title": "The Zuni Café Cookbook", "recipes": [
    {"id": 1, "name": "Zuni Roast Chicken with Bread Salad", "page": "342"},
    {"id": 2, "name": "Zuni Caesar Salad", "page": None},
    {"id": 3, "name": "  ", "page": "1"}]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def catalog(monkeypatch, a, routes, calls=None):
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:8767")

    def fake_urlopen(url, timeout=None):
        if calls is not None:
            calls.append(url)
        path = url.split("8767", 1)[1]
        if path not in routes:
            raise urllib.error.URLError("down")
        return Resp(json.dumps(routes[path]).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_title_key(tmp_path):
    a = load_app(tmp_path)
    assert a.title_key("The Zuni Café Cookbook") == a.title_key("  zuni  cafe cookbook ") == "zuni cafe cookbook"
    assert a.title_key("A Year of Pies") == "year of pies"
    assert a.title_key("") == ""


def test_recipes_for_an_indexed_book(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    calls = []
    catalog(monkeypatch, a, {"/api/books": BOOKS, "/api/books/7": ZUNI}, calls)
    book, book_id, recipes = a.cookbook_recipes("the zuni cafe cookbook")
    assert book == "The Zuni Café Cookbook" and book_id == 7
    assert recipes == [{"id": 1, "name": "Zuni Roast Chicken with Bread Salad", "page": "342"},
                       {"id": 2, "name": "Zuni Caesar Salad", "page": ""}]          # blank names dropped
    a.cookbook_recipes("The Zuni Café Cookbook")
    assert calls.count("http://127.0.0.1:8767/api/books/7") == 1             # cached


def test_unknown_unindexed_or_down(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    catalog(monkeypatch, a, {"/api/books": BOOKS})                            # /api/books/9 fails
    assert a.cookbook_recipes("Grandma's binder") == (None, None, [])
    assert a.cookbook_recipes("Dining In") == (None, None, [])
    assert a.cookbook_recipes("") == (None, None, [])


def test_route_and_form(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    catalog(monkeypatch, a, {"/api/books": BOOKS, "/api/books/7": ZUNI})
    c = a.app.test_client()
    r = c.get("/cookbook/recipes?title=Zuni%20Cafe%20Cookbook").get_json()
    assert r["book"] == "The Zuni Café Cookbook" and len(r["recipes"]) == 2
    assert r["book_id"] == 7 and r["recipes"][0]["id"] == 1
    assert c.get("/cookbook/recipes?title=").get_json() == {"book": None, "book_id": None, "recipes": []}
    page = c.get("/new").get_data(as_text=True)
    assert 'id="recipe-input"' in page and "/cookbook/recipes" in page


def test_route_without_catalog(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "")
    assert a.app.test_client().get("/cookbook/recipes?title=Dining%20In").get_json() == {"book": None, "book_id": None, "recipes": []}
