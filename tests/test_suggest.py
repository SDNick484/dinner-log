import datetime

from test_app import load_app, text

TODAY = datetime.date(2026, 10, 28)


def add(app_module, day, name, source_type="original", rating=None, source_title=""):
    app_module.db().execute(
        "INSERT INTO meals (day, name, source_type, source_title, rating) VALUES (?, ?, ?, ?, ?)",
        (day, name, source_type, source_title, rating))
    app_module.db().commit()


def test_suggestion_respects_source_filter(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-08-01", "Pad thai", "restaurant", 9)
        add(a, "2026-08-02", "Lasagna", "cookbook", 9, "Some Book")
        picks = {a.suggest_dinner(today=TODAY, source="cookbook")["name"] for _ in range(30)}
        assert picks == {"Lasagna"}


def test_suggestion_respects_favorites_filter(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-08-01", "Meh soup", rating=5)
        assert a.suggest_dinner(today=TODAY, fav=True) is None
        add(a, "2026-08-02", "Great curry", rating=9)
        assert a.suggest_dinner(today=TODAY, fav=True)["name"] == "Great curry"


def test_favorite_counts_best_rating_not_latest(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-07-01", "Gumbo", rating=10)
        add(a, "2026-07-15", "Gumbo")              # made again, not rated that time
        add(a, "2026-08-01", "Plain rice", rating=4)
        picks = {a.suggest_dinner(today=TODAY)["name"] for _ in range(30)}
        assert picks == {"Gumbo"}                  # priority 1 = best-ever rating >= 8
        s = a.suggest_dinner(today=TODAY)
        assert s["day"] == "2026-07-15"            # still reports the latest time it was made
        assert s["days_ago"] == (TODAY - datetime.date(2026, 7, 15)).days


def test_index_passes_filters_to_suggestion(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        add(a, "2026-08-01", "Pad thai", "restaurant", 9)
    # Only a restaurant exists; filtering to cookbooks must not suggest it.
    html = text(c.get("/?source=cookbook&suggest=1"))
    assert "Pad thai" not in html


def test_source_filter_still_respects_recency_across_sources(tmp_path):
    # Same dish logged from the book and later as our own riff, last night.
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-08-01", "Lasagna", "cookbook", 9, "Some Book")
        add(a, "2026-10-27", "Lasagna", "original", 8)
        assert a.suggest_dinner(today=TODAY, source="cookbook") is None


def test_source_filter_reports_true_last_made_and_matching_source(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-06-01", "Lasagna", "cookbook", 9, "Some Book")
        add(a, "2026-08-01", "Lasagna", "original", 7)
        s = a.suggest_dinner(today=TODAY, source="cookbook")
        assert s["source_type"] == "cookbook" and s["source_title"] == "Some Book"
        assert s["days_ago"] == (TODAY - datetime.date(2026, 8, 1)).days


# ------------------------------------------------ ingredients, modes, Cookbook Collection

def catalog_reply(*recipes, linked=(), title_only=()):
    return {"recipes": list(recipes), "linked_dinners": list(linked), "title_only": list(title_only)}


def recipe(name, book="Some Book", page="12", best=None, last=None, times=0, online=False, url=None, starred=False):
    return {"name": name, "book_title": book, "page": page, "url": url, "online": online, "starred": starred,
            "best_rating": best, "last_cooked": last, "times_cooked": times}


def test_parse_terms_and_mentions(tmp_path):
    a = load_app(tmp_path)
    assert a.parse_terms(" chicken,  Leeks ,,CHICKEN, ground   beef") == ["chicken", "Leeks", "ground beef"]
    assert len(a.parse_terms(",".join("abcdefgh"))) == a.PICK_MAX_TERMS
    assert a.mentions("Leek and Potato Soup", "leeks")
    assert a.mentions("Cherry tomatoes on toast", "tomato")
    assert not a.mentions("Peach pie", "pea")
    assert a.mentions("Crème brûlée", "creme")
    assert a.mentions("Ground beef chili", "ground beef") and not a.mentions("Beef stew", "ground beef")


def test_local_ingredient_filter_without_catalog(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-08-01", "Chicken tikka", rating=9)
        add(a, "2026-08-02", "Pork ragu", rating=9)
        add(a, "2026-08-03", "Rice bowl", rating=9)
        a.db().execute("UPDATE meals SET notes='added leftover chicken' WHERE name='Rice bowl'")
        a.db().commit()
        picks = {a.suggest_dinner(today=TODAY, include=["chicken"])["name"] for _ in range(40)}
        assert picks == {"Chicken tikka", "Rice bowl"}             # notes count toward "with"
        picks = {a.suggest_dinner(today=TODAY, exclude=["pork"])["name"] for _ in range(40)}
        assert picks == {"Chicken tikka", "Rice bowl"}
        s, info = a.pick_for_tonight(today=TODAY, mode="new")
        assert s is None and info["catalog"] == "off"             # "new" needs Cookbook Collection


def test_new_mode_comes_from_catalog(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    calls = []

    def fake(mode, include, exclude, rest=21, **kw):
        calls.append((mode, list(include), list(exclude)))
        return catalog_reply(recipe("Zuni Roast Chicken", "The Zuni Cafe Cookbook", "342", starred=True),
                             title_only=["pork"])
    monkeypatch.setattr(a, "fetch_cookbook_picks", fake)
    with a.app.app_context():
        s, info = a.pick_for_tonight(today=TODAY, mode="new", include=["chicken"], exclude=["pork"])
    assert calls == [("new", ["chicken"], ["pork"])]
    assert s["name"] == "Zuni Roast Chicken" and s["source_type"] == "cookbook"
    assert s["source_title"] == "The Zuni Cafe Cookbook" and s["source_detail"] == "342"
    assert s["is_new"] and s["starred"] and s["days_ago"] is None
    assert info == {"mode": "new", "catalog": "ok", "title_only": ["pork"]}


def test_favorites_leave_linked_dishes_to_the_catalog(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: catalog_reply(
        recipe("Zuni Roast Chicken with Bread Salad", best=10.0, last="2026-07-01", times=3),
        linked=["Zuni chicken"]))
    with a.app.app_context():
        add(a, "2026-07-01", "Zuni chicken", "cookbook", 10, "The Zuni Cafe Cookbook")  # matched by the catalog
        add(a, "2026-07-02", "Taco night", "original", 9)
        picks = {a.suggest_dinner(today=TODAY, mode="fav")["name"] for _ in range(40)}
        assert picks == {"Zuni Roast Chicken with Bread Salad", "Taco night"}
        s = next(x for x in (a.suggest_dinner(today=TODAY, mode="fav") for _ in range(40))
                 if x["name"].startswith("Zuni"))
        assert s["rating"] == 10 and isinstance(s["rating"], int) and not s["is_new"]
        assert s["days_ago"] == (TODAY - datetime.date(2026, 7, 1)).days


def test_catalog_down_falls_back_to_past_dinners(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: None)
    with a.app.app_context():
        add(a, "2026-08-01", "Zuni chicken", "cookbook", 9, "The Zuni Cafe Cookbook")
        s, info = a.pick_for_tonight(today=TODAY, mode="fav")
    assert s["name"] == "Zuni chicken" and info["catalog"] == "down"


def test_restaurant_chip_skips_the_catalog(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: (_ for _ in ()).throw(AssertionError("called")))
    with a.app.app_context():
        add(a, "2026-08-01", "Pad thai", "restaurant", 9)
        s, info = a.pick_for_tonight(today=TODAY, source="restaurant")
    assert s["name"] == "Pad thai" and info["catalog"] == "skipped"


def test_online_chip_keeps_only_web_recipes(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: catalog_reply(
        recipe("Book dish"),
        recipe("French Onion Soup", "NYT Cooking", None, online=True, url="https://cooking.example.com/r/1")))
    with a.app.app_context():
        s = a.suggest_dinner(today=TODAY, source="online", mode="new")
    assert s["name"] == "French Onion Soup" and s["source_type"] == "online"
    assert s["source_detail"] == "https://cooking.example.com/r/1"


class FirstHalf:
    """rng stub: the coin flip says 'past dinner first'; choice takes the first item."""
    def random(self):
        return 0.1

    def choice(self, xs):
        return xs[0]


class SecondHalf(FirstHalf):
    def random(self):
        return 0.9


def test_surprise_me_flips_between_favorites_and_new(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda mode, *a_, **k: catalog_reply(
        *([recipe("Never made")] if mode == "new" else [])))
    with a.app.app_context():
        add(a, "2026-08-01", "Gumbo", "original", 10)
        assert a.suggest_dinner(today=TODAY, rng=FirstHalf())["name"] == "Gumbo"
        assert a.suggest_dinner(today=TODAY, rng=SecondHalf())["name"] == "Never made"


def test_fetch_sends_token_and_never_follows_redirects(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:8767")
    monkeypatch.setattr(a, "COOKBOOK_TOKEN", "s3cret")
    seen = {}

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    class Opener:
        def open(self, req, timeout=None):
            seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
            return Resp(b'{"recipes": []}')
    monkeypatch.setattr(a, "_cookbook_opener", Opener())
    assert a.fetch_cookbook_picks("fav", ["chicken", "leeks"], ["pork"]) == {"recipes": []}
    assert seen["auth"] == "Bearer s3cret"
    assert seen["url"].startswith("http://127.0.0.1:8767/admin/api/pick?mode=fav&with=chicken%2Cleeks&without=pork")

    class LoginPage(Opener):
        def open(self, req, timeout=None):
            return Resp(b"<html>sign in</html>")
    monkeypatch.setattr(a, "_cookbook_opener", LoginPage())
    assert a.fetch_cookbook_picks("fav") is None
    # a redirect (an access proxy's login page) is refused instead of followed with the token
    assert a._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://login.example.com/") is None


def test_index_picker_keeps_its_settings(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        add(a, "2026-08-01", "Chicken tikka", rating=9)
    html = text(c.get("/?suggest=1&mode=fav&with=chicken&without=pork"))
    assert "Chicken tikka" in html
    assert "with=chicken" in html and "without=pork" in html and "mode=fav" in html  # Roll again / chips
    assert 'value="fav" checked' in html
    html = text(c.get("/?suggest=1&with=lobster"))
    assert "Nothing fits that." in html
    html = text(c.get("/?suggest=1&mode=new"))
    assert "isn't connected" in html


def test_catalog_dish_keeps_ids(tmp_path):
    a = load_app(tmp_path)
    d = a.catalog_dish({**recipe("Roast Chicken", "Zuni", "342"), "id": 2203, "book_id": 17}, TODAY)
    assert (d["cookbook_book_id"], d["cookbook_recipe_id"]) == (17, 2203)
    d = a.catalog_dish(recipe("No ids"), TODAY)                       # an older Cookbook Collection
    assert (d["cookbook_book_id"], d["cookbook_recipe_id"]) == (None, None)


def test_log_tonight_link_carries_ids(tmp_path, monkeypatch):
    a = load_app(tmp_path)
    monkeypatch.setattr(a, "COOKBOOK_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(a, "fetch_cookbook_picks", lambda *a_, **k: catalog_reply(
        {**recipe("Zuni Roast Chicken", "The Zuni Cafe Cookbook", "342"), "id": 2203, "book_id": 17}))
    html = a.app.test_client().get("/?suggest=1&mode=new").get_data(as_text=True)
    assert "cookbook_book_id=17" in html and "cookbook_recipe_id=2203" in html


def test_local_dish_link_has_no_ids(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    c.post("/new", data={"name": "Tacos", "source_type": "cookbook", "source_title": "Mexican Everyday",
                         "rating": "9", "day": "2026-09-01", "cookbook_book_id": "5", "cookbook_recipe_id": "6"})
    html = c.get("/?suggest=1").get_data(as_text=True)
    log_href = html.split('suggest-log-btn" href="', 1)[1].split('"', 1)[0]
    assert "name=Tacos" in log_href and "cookbook_" not in log_href
