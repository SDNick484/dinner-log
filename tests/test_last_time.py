"""The "Last time" panel: which past dinners count as the same dish, and what the panel says about them."""
import datetime

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


# ------------------------------------------------------------------ the panel's wording
def test_summary_wording(tmp_path):
    a = load_app(tmp_path)
    s = a.last_time_summary
    assert s(6, 9) == "made 6 times · best 9/10"
    assert s(1, 8) == "made once · best 8/10"
    assert s(3, None) == "made 3 times"
    assert s(6, 9, other=True) == "made 6 other times · best 9/10"
    assert s(1, None, other=True) == "made 1 other time"
    assert s(0, None) == ""


def test_panel_entries_are_clipped_dated_and_linked(tmp_path):
    a = load_app(tmp_path)
    with a.app.test_request_context():
        long_note = "Needs   more\nsalt. " + "word " * 60
        i = add(a, "2026-10-01", "Flatbread", 8, long_note)
        j = add(a, "2026-10-02", "Flatbread", None, "short note")
        panel = a.last_time_panel(None, "Flatbread")
        assert panel["summary"] == "made 2 times · best 8/10"
        newest, older = panel["recent"]
        assert newest["notes"] == "short note" and newest["rating"] is None
        assert newest["when"] == "Fri Oct 2, 2026" and newest["url"] == f"/meal/{j}"
        assert older["url"] == f"/meal/{i}"
        assert len(older["notes"]) <= 140 and older["notes"].endswith("…")
        assert older["notes"].startswith("Needs more salt. word word")   # whitespace collapsed


# ------------------------------------------------------------------ detail page and pick card
def test_detail_page_lists_other_times_newest_first_with_dates(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        old = add(a, "2026-01-05", "Gumbo", 7, "A bit thin")
        this = add(a, "2026-03-09", "Gumbo", 9, "The one")
        new = add(a, "2026-06-01", "Gumbo", 8, "Even better <b>x</b>")
    page = c.get(f"/meal/{this}").get_data(as_text=True)
    assert "Last time" in page and "made 2 other times · best 8/10" in page    # best of the others, not this one
    assert page.index("Mon Jun 1, 2026") < page.index("Mon Jan 5, 2026")       # newer one listed too, newest first
    assert f'href="/meal/{old}"' in page and f'href="/meal/{new}"' in page
    assert page.count("The one") == 1                                           # the dinner itself only in its own notes
    assert "A bit thin" in page and "Even better &lt;b&gt;x&lt;/b&gt;" in page   # notes are escaped


def test_detail_page_of_a_first_time_dish_has_no_panel(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        first = add(a, "2026-01-05", "Gumbo", 7, "Solo")
        add(a, "2026-01-06", "Something else")
    assert "Last time" not in c.get(f"/meal/{first}").get_data(as_text=True)


def test_pick_card_shows_every_time_the_dish_was_made(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    today = a.local_today()
    with a.app.app_context():
        add(a, (today - datetime.timedelta(days=100)).isoformat(), "Gumbo", 7, "Needed salt")
        add(a, (today - datetime.timedelta(days=60)).isoformat(), "Gumbo", 9, "Perfect")
    page = c.get("/?suggest=1").get_data(as_text=True)
    assert "Tonight's Idea" in page and "Last time" in page
    assert "made 2 times · best 9/10" in page and "Needed salt" in page and "Perfect" in page


def test_pick_card_without_history_has_no_panel(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    page = c.get("/?suggest=1").get_data(as_text=True)
    assert "Last time" not in page


# ------------------------------------------------------------------ the JSON route behind the form
def test_route_shape(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        i = add(a, "2026-10-01", "Gumbo", 8, "Good")
        j = add(a, "2026-10-09", "Gumbo", 6, "Meh")
    r = c.get("/last-time?name=gumbos")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert r.headers["Cache-Control"] == "private, no-store"
    assert r.get_json() == {
        "count": 2, "best": 8, "summary": "made 2 times · best 8/10",
        "recent": [
            {"id": j, "day": "2026-10-09", "when": "Fri Oct 9, 2026", "rating": 6, "notes": "Meh", "url": f"/meal/{j}"},
            {"id": i, "day": "2026-10-01", "when": "Thu Oct 1, 2026", "rating": 8, "notes": "Good", "url": f"/meal/{i}"}]}


def test_route_matches_by_uid_and_excludes_the_dinner_being_edited(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        i = add(a, "2026-10-01", "Weeknight carbonara", 8, uid=U1, source_type="cookbook")
        j = add(a, "2026-10-09", "Carbonara", 9, uid=U1, source_type="cookbook")
        add(a, "2026-10-10", "Carbonara", 3, uid=U2, source_type="cookbook")      # a different recipe
    data = c.get(f"/last-time?uid={U1}&name=Carbonara&exclude={j}").get_json()
    assert [t["id"] for t in data["recent"]] == [i]
    assert data["summary"] == "made 1 other time · best 8/10"                   # editing: it's one of them
    assert c.get(f"/last-time?uid={U1}&name=Carbonara").get_json()["summary"] == "made 2 times · best 9/10"


def test_route_empty_case(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        add(a, "2026-10-01", "Gumbo")
    empty = {"count": 0, "best": None, "summary": "", "recent": []}
    assert c.get("/last-time?name=Lasagna").get_json() == empty
    assert c.get("/last-time").get_json() == empty


def test_route_survives_junk_input(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        add(a, "2026-10-01", "Gumbo", 8)
    for qs in ("uid=zzz&name=Gumbo&exclude=abc", "exclude=-1&name=Gumbo", "name=" + "x" * 50_000,
               "uid=%00&name=%00", "exclude=" + "9" * 40 + "&name=Gumbo"):
        assert c.get("/last-time?" + qs).status_code == 200, qs
    assert c.get("/last-time?uid=zzz&name=Gumbo&exclude=abc").get_json()["count"] == 1   # junk is ignored


def test_route_never_sends_more_than_the_clipped_notes(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    with a.app.app_context():
        add(a, "2026-10-01", "Gumbo", 8, "secret " * 100)
    note = c.get("/last-time?name=Gumbo").get_json()["recent"][0]["notes"]
    assert len(note) <= 140 and note.endswith("…")


def test_form_has_the_panel_and_the_route(tmp_path):
    a = load_app(tmp_path)
    c = a.app.test_client()
    for url in ("/new", "/new?name=Gumbo"):
        page = c.get(url).get_data(as_text=True)
        assert 'id="last-time"' in page and "/last-time" in page and "hidden" in page.split('id="last-time"')[1][:80]
    with a.app.app_context():
        i = add(a, "2026-10-01", "Gumbo")
    assert f"const editId = '{i}'" in c.get(f"/meal/{i}/edit").get_data(as_text=True)
    assert "const editId = ''" in c.get("/new").get_data(as_text=True)


def test_service_worker_never_caches_the_route(tmp_path):
    """sw.js ignores Cache-Control, so without this each typed name would leave notes in Cache Storage."""
    a = load_app(tmp_path)
    sw = a.app.test_client().get("/sw.js").get_data(as_text=True)
    assert "'/last-time'" in sw
