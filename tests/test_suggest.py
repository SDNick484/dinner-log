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
