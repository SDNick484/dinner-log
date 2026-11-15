"""GET /api/restaurant-visits: the restaurant dinners feed Hometown Table pulls, behind VISITS_TOKEN."""
import pytest

from test_app import load_app

TOKEN = "v" * 64
AUTH = {"Authorization": "Bearer " + TOKEN}
FIELDS = {"ref", "day", "name", "location", "dish"}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("VISITS_TOKEN", TOKEN)
    return load_app(tmp_path)


def add(a, day, name, source_type="restaurant", title="", detail="", notes="", rating=None, cookbook_book_id=None):
    with a.app.app_context():
        cur = a.db().execute(
            "INSERT INTO meals (day, name, source_type, source_title, source_detail, notes, rating, cookbook_book_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (day, name, source_type, title, detail, notes, rating, cookbook_book_id))
        a.db().commit()
        return cur.lastrowid


def get(a, query="", headers=AUTH):
    return a.app.test_client().get("/api/restaurant-visits" + query, headers=headers)


def test_404_while_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("VISITS_TOKEN", raising=False)
    a = load_app(tmp_path)
    assert get(a).status_code == 404
    assert get(a, headers={}).status_code == 404


def test_history_token_does_not_open_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORY_TOKEN", "h" * 64)
    monkeypatch.setenv("VISITS_TOKEN", TOKEN)
    a = load_app(tmp_path)
    assert get(a, headers={"Authorization": "Bearer " + "h" * 64}).status_code == 401
    # and the visits token does not open the history feed
    r = a.app.test_client().get("/api/history", headers=AUTH)
    assert r.status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN},
                                     {"Authorization": "Basic " + TOKEN}, {"Authorization": "Bearer "}])
def test_401_without_or_with_wrong_token(app, headers):
    r = get(app, headers=headers)
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_contract_shape(app):
    mid = add(app, "2026-11-14", "Al pastor tacos", title="Casa Ejemplo", detail="Concord")
    r = get(app)
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert "no-store" in r.headers["Cache-Control"]
    assert r.get_json() == {"version": 1, "next": mid, "visits": [
        {"ref": str(mid), "day": "2026-11-14", "name": "Casa Ejemplo", "location": "Concord",
         "dish": "Al pastor tacos"}]}
    assert isinstance(r.get_json()["visits"][0]["ref"], str)


def test_only_restaurant_meals_with_a_title(app):
    keep = add(app, "2026-11-01", "Pizza", title="Pizzeria Ejemplo")
    add(app, "2026-11-02", "Stew", source_type="cookbook", title="Soup Book", detail="p. 4")
    add(app, "2026-11-03", "Soup", source_type="original", title="Granny")
    add(app, "2026-11-04", "Salad", source_type="online", title="example.com")
    add(app, "2026-11-05", "Mystery", title="")
    add(app, "2026-11-06", "Blank", title="   ")
    r = get(app).get_json()
    assert [v["ref"] for v in r["visits"]] == [str(keep)] and r["next"] == keep


def test_nulls_for_empty_location_and_dish(app):
    add(app, "2026-11-01", "", title="Cafe Ejemplo")
    (v,) = get(app).get_json()["visits"]
    assert v["location"] is None and v["dish"] is None and v["name"] == "Cafe Ejemplo"


def test_exact_field_set_no_leaks(app):
    add(app, "2026-11-01", "Noodles", title="Noodle Haus Ejemplo", detail="Dayton", notes="SECRET NOTE",
        rating=9, cookbook_book_id=7)
    body = get(app).get_data(as_text=True)
    assert "SECRET NOTE" not in body and "rating" not in body and "photos" not in body
    r = get(app).get_json()
    assert set(r) == {"version", "visits", "next"}
    assert all(set(v) == FIELDS for v in r["visits"])


def test_order_after_limit_next(app):
    ids = [add(app, f"2026-11-{i + 1:02d}", f"Dish {i}", title=f"Place {i}") for i in range(5)]
    add(app, "2026-11-20", "Home", source_type="original", title="x")          # a gap in ids
    ids.append(add(app, "2026-10-01", "Older day, later id", title="Place 5"))  # ordered by id, not day
    r = get(app).get_json()
    assert [v["ref"] for v in r["visits"]] == [str(i) for i in ids] and r["next"] == ids[-1]

    page = get(app, "?limit=2").get_json()
    assert [v["ref"] for v in page["visits"]] == [str(ids[0]), str(ids[1])] and page["next"] == ids[1]
    page = get(app, f"?after={page['next']}&limit=2").get_json()
    assert [v["ref"] for v in page["visits"]] == [str(ids[2]), str(ids[3])] and page["next"] == ids[3]
    page = get(app, f"?after={page['next']}&limit=2").get_json()
    assert [v["ref"] for v in page["visits"]] == [str(ids[4]), str(ids[5])]
    done = get(app, f"?after={ids[-1]}").get_json()
    assert done == {"version": 1, "visits": [], "next": ids[-1]}
    assert get(app, "?after=999").get_json()["next"] == 999


def test_empty_feed_next_is_after_default_zero(app):
    assert get(app).get_json() == {"version": 1, "visits": [], "next": 0}


def test_limit_bounds_and_bad_input(app):
    for i in range(3):
        add(app, "2026-11-01", f"D{i}", title="P")
    assert len(get(app, "?limit=0").get_json()["visits"]) == 1            # clamped to 1
    assert len(get(app, "?limit=100000").get_json()["visits"]) == 3       # clamped to 500
    for q in ("?after=x", "?after=-1", "?limit=abc", "?after=1.5", "?after=", "?after=" + "9" * 30):
        assert get(app, q).status_code == 400, q


def test_limit_is_capped_at_500(app):
    a = app
    with a.app.app_context():
        a.db().executemany("INSERT INTO meals (day, name, source_type, source_title) VALUES ('2026-11-01', 'd', "
                           "'restaurant', 'P')", [()] * 510)
        a.db().commit()
    assert len(get(a, "?limit=500").get_json()["visits"]) == 500
    assert len(get(a, "?limit=9999").get_json()["visits"]) == 500
    assert len(get(a).get_json()["visits"]) == 200                        # default
