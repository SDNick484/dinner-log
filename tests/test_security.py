import pytest

from test_app import load_app


@pytest.fixture()
def c(tmp_path):
    return load_app(tmp_path).app.test_client()


def test_cross_site_post_is_refused(c):
    resp = c.post("/new", data={"name": "Evil"}, headers={"Origin": "https://attacker.example"})
    assert resp.status_code == 403


def test_null_origin_post_is_refused(c):
    assert c.post("/new", data={"name": "Evil"}, headers={"Origin": "null"}).status_code == 403


def test_cross_site_fetch_metadata_is_refused(c):
    resp = c.post("/meal/1/delete", headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403


def test_same_origin_post_is_allowed(c):
    resp = c.post("/new", data={"name": "Tacos"},
                  headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 302


def test_post_without_origin_headers_is_allowed(c):
    # Non-browser clients and older browsers send neither header.
    assert c.post("/new", data={"name": "Tacos"}).status_code == 302


def test_cross_site_get_is_unaffected(c):
    assert c.get("/", headers={"Origin": "https://elsewhere.example"}).status_code == 200


def test_extra_trusted_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTED_ORIGINS", "https://alt.example")
    c = load_app(tmp_path).app.test_client()
    assert c.post("/new", data={"name": "x"}, headers={"Origin": "https://alt.example"}).status_code == 302
