import datetime
import re

from test_app import load_app, text


def seed(a, n, start=datetime.date(2025, 1, 1), source_type="original"):
    with a.app.app_context():
        for i in range(n):
            a.db().execute("INSERT INTO meals (day, name, source_type) VALUES (?, ?, ?)",
                           ((start + datetime.timedelta(days=i)).isoformat(), f"Meal {i:03d}", source_type))
        a.db().commit()


def names(html):
    return re.findall(r'<div class="name">(Meal \d+)</div>', html)


def older_link(html):
    m = re.search(r'<a class="older" href="([^"]+)"', html)
    return m.group(1).replace("&amp;", "&") if m else None


def test_feed_pages_through_every_meal(tmp_path):
    a = load_app(tmp_path)
    a.PAGE_SIZE = 4
    seed(a, 10)
    c = a.app.test_client()
    seen, url, pages = [], "/", 0
    while url:
        html = text(c.get(url))
        assert len(names(html)) <= 4
        seen += names(html)
        url = older_link(html)
        pages += 1
    assert pages == 3
    assert seen == [f"Meal {i:03d}" for i in range(9, -1, -1)]  # newest first, nothing lost


def test_same_day_meals_split_across_pages_are_not_lost(tmp_path):
    a = load_app(tmp_path)
    a.PAGE_SIZE = 2
    with a.app.app_context():
        for i in range(3):
            a.db().execute("INSERT INTO meals (day, name) VALUES ('2026-01-01', ?)", (f"Meal {i:03d}",))
        a.db().commit()
    c = a.app.test_client()
    first = text(c.get("/"))
    second = text(c.get(older_link(first)))
    assert sorted(names(first) + names(second)) == ["Meal 000", "Meal 001", "Meal 002"]


def test_no_older_link_on_last_page(tmp_path):
    a = load_app(tmp_path)
    a.PAGE_SIZE = 4
    seed(a, 4)
    assert older_link(text(a.app.test_client().get("/"))) is None


def test_older_link_keeps_filters(tmp_path):
    a = load_app(tmp_path)
    a.PAGE_SIZE = 2
    seed(a, 5, source_type="cookbook")
    link = older_link(text(a.app.test_client().get("/?source=cookbook&q=Meal")))
    assert "source=cookbook" in link and "q=Meal" in link


def test_bad_cursor_falls_back_to_first_page(tmp_path):
    a = load_app(tmp_path)
    seed(a, 3)
    resp = a.app.test_client().get("/?before=garbage")
    assert resp.status_code == 200
    assert "Meal 002" in text(resp)
