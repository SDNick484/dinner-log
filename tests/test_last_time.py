"""The "Last time" panel: which past dinners count as the same dish, and what the panel says about them."""
from test_app import load_app

U1 = "a" * 32
U2 = "b" * 32


def add(a, day, name, rating=None, notes="", uid=None, source_type="original"):
    cur = a.db().execute(
        "INSERT INTO meals (day, name, rating, notes, cookbook_recipe_uid, source_type) VALUES (?, ?, ?, ?, ?, ?)",
        (day, name, rating, notes, uid, source_type))
    a.db().commit()
    return cur.lastrowid


def days(result):
    return [t["day"] for t in result["recent"]]


# ------------------------------------------------------------------ matching
def test_uid_matches_whatever_the_dinner_was_called(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Weeknight carbonara", uid=U1, source_type="cookbook")
        add(a, "2026-09-08", "Spaghetti alla carbonara", uid=U1, source_type="cookbook")
        add(a, "2026-09-09", "Cacio e pepe", uid=U2, source_type="cookbook")
        r = a.previous_times(U1, "Carbonara (our version)")
        assert days(r) == ["2026-09-08", "2026-09-01"] and r["count"] == 2


def test_name_ignores_case_whitespace_and_plurals(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Tomato soup")
        add(a, "2026-09-02", "  TOMATO   soup ")
        add(a, "2026-09-03", "Tomatoes soup")
        add(a, "2026-09-04", "Leek tart")
        add(a, "2026-09-05", "Leeks tart")
        assert a.previous_times(None, "tomato soup")["count"] == 3
        assert a.previous_times(None, "Leek Tarts")["count"] == 2


def test_name_must_match_whole_and_in_order(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Chicken tikka masala")
        add(a, "2026-09-02", "Chicken tikka")
        add(a, "2026-09-03", "Masala tikka chicken")
        assert days(a.previous_times(None, "Chicken tikka")) == ["2026-09-02"]


def test_accents_fold_and_other_scripts_do_not_collide(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Tagliatelle al ragù")
        add(a, "2026-09-02", "ラーメン")
        add(a, "2026-09-03", "うどん")
        assert days(a.previous_times(None, "tagliatelle al ragu")) == ["2026-09-01"]
        assert days(a.previous_times(None, "ラーメン")) == ["2026-09-02"]   # not every non-ASCII name at once


def test_same_name_with_a_different_uid_is_a_different_recipe(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Chicken soup", uid=U2, source_type="cookbook")
        add(a, "2026-09-02", "Chicken soup", uid=U1, source_type="cookbook")
        assert days(a.previous_times(U1, "Chicken soup")) == ["2026-09-02"]
        assert days(a.previous_times(U2, "Chicken soup")) == ["2026-09-01"]


def test_a_uid_dish_also_matches_older_dinners_without_one_by_name(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-08-01", "Pad Thai")                       # before uids existed
        add(a, "2026-08-15", "Pad Thai", uid=U2, source_type="cookbook")   # a different recipe
        add(a, "2026-08-20", "Green curry")
        assert days(a.previous_times(U1, "pad thai")) == ["2026-08-01"]


def test_a_dish_without_a_uid_matches_by_name_whatever_the_rows_carry(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        old = add(a, "2026-08-01", "Pad Thai")
        new = add(a, "2026-09-01", "Pad Thai", uid=U1, source_type="cookbook")
        # the relation is symmetric: each sees the other
        assert days(a.previous_times(None, "Pad Thai", exclude_id=old)) == ["2026-09-01"]
        assert days(a.previous_times(U1, "Pad Thai", exclude_id=new)) == ["2026-08-01"]


def test_malformed_stored_uid_counts_as_no_uid(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Pad Thai", uid="not-a-uid")
        assert a.previous_times(U1, "Pad Thai")["count"] == 1


def test_the_dinner_itself_is_excluded(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        first = add(a, "2026-09-01", "Gumbo", 7)
        second = add(a, "2026-09-08", "Gumbo", 9)
        r = a.previous_times(None, "Gumbo", exclude_id=second)
        assert [t["id"] for t in r["recent"]] == [first] and r["count"] == 1 and r["best"] == 7
        assert a.previous_times(None, "Gumbo")["count"] == 2


def test_newer_dinners_are_listed_too(tmp_path):
    """Viewing an old dinner lists the later ones; exclusion is by id only."""
    a = load_app(tmp_path)
    with a.app.app_context():
        old = add(a, "2026-01-01", "Gumbo")
        add(a, "2026-06-01", "Gumbo")
        assert days(a.previous_times(None, "Gumbo", exclude_id=old)) == ["2026-06-01"]


# ------------------------------------------------------------------ count, order, limit, best
def test_newest_first_limit_and_totals_span_every_match(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        for i, rating in enumerate([6, 9, None, 7, 5], start=1):
            add(a, f"2026-09-0{i}", "Lasagna", rating)
        r = a.previous_times(None, "Lasagna")
        assert days(r) == ["2026-09-05", "2026-09-04", "2026-09-03"]    # limit=3, newest first
        assert r["count"] == 5 and r["best"] == 9                        # beyond the shown three
        assert len(a.previous_times(None, "Lasagna", limit=1)["recent"]) == 1
        assert len(a.previous_times(None, "Lasagna", limit=10)["recent"]) == 5


def test_same_day_dishes_order_by_id_descending(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        first = add(a, "2026-09-01", "Flatbread")
        second = add(a, "2026-09-01", "Flatbread")
        assert [t["id"] for t in a.previous_times(None, "Flatbread")["recent"]] == [second, first]


def test_best_is_none_when_nothing_was_rated(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Flatbread")
        r = a.previous_times(None, "Flatbread")
        assert r["count"] == 1 and r["best"] is None and r["recent"][0]["rating"] is None


def test_entries_carry_day_rating_and_full_notes(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        i = add(a, "2026-09-01", "Flatbread", 8, "Needs more salt")
        assert a.previous_times(None, "Flatbread")["recent"] == [
            {"id": i, "day": "2026-09-01", "rating": 8, "notes": "Needs more salt"}]


# ------------------------------------------------------------------ empty cases
def test_nothing_matches(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Flatbread")
        assert a.previous_times(None, "Gumbo") == {"count": 0, "best": None, "recent": []}


def test_no_uid_and_no_name_matches_nothing(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        add(a, "2026-09-01", "Flatbread")
        add(a, "2026-09-02", "")
        for name in ("", "   ", None):
            assert a.previous_times(None, name)["count"] == 0
        assert a.previous_times("garbage", "")["count"] == 0     # an invalid uid is no uid


def test_empty_database(tmp_path):
    a = load_app(tmp_path)
    with a.app.app_context():
        assert a.previous_times(U1, "Anything") == {"count": 0, "best": None, "recent": []}
