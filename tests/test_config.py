import pytest

from test_app import load_app, text


@pytest.mark.parametrize("value", ["", "Not/AZone"])
def test_bad_app_tz_falls_back_to_utc(tmp_path, monkeypatch, value):
    monkeypatch.setenv("APP_TZ", value)
    a = load_app(tmp_path)                      # must not crash at import
    assert a.APP_TZ.key == "UTC"


def test_compact_iso_date_is_stored_normalized(tmp_path):
    # Python 3.11+ fromisoformat accepts "20261028"; stored as-is it would sort wrongly.
    a = load_app(tmp_path)
    c = a.app.test_client()
    c.post("/new", data={"name": "Soup", "day": "20261028"})
    with a.app.app_context():
        assert a.db().execute("SELECT day FROM meals").fetchone()[0] == "2026-10-28"


def test_cursor_date_is_normalized(tmp_path):
    a = load_app(tmp_path)
    assert a.parse_cursor("20261028.5") == ("2026-10-28", 5)
