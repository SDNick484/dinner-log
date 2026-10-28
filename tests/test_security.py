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


def test_same_origin_post_behind_host_rewriting_proxy_is_allowed(c):
    # e.g. nginx's default proxy_set_header Host $proxy_host
    resp = c.post("/new", data={"name": "Tacos"},
                  headers={"Host": "127.0.0.1:8000", "Origin": "https://dinners.example.com",
                           "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 302


def test_same_site_post_is_refused_unless_trusted(tmp_path, monkeypatch):
    hdrs = {"Origin": "https://other.example.com", "Sec-Fetch-Site": "same-site"}
    c = load_app(tmp_path).app.test_client()
    assert c.post("/new", data={"name": "x"}, headers=hdrs).status_code == 403
    monkeypatch.setenv("TRUSTED_ORIGINS", "https://other.example.com")
    c = load_app(tmp_path).app.test_client()
    assert c.post("/new", data={"name": "x"}, headers=hdrs).status_code == 302


def test_origin_fallback_accepts_forwarded_host(c):
    # No Fetch Metadata (older browser), proxy rewrote Host but sent X-Forwarded-Host.
    resp = c.post("/new", data={"name": "Tacos"},
                  headers={"Host": "127.0.0.1:8000", "X-Forwarded-Host": "dinners.example.com",
                           "Origin": "https://dinners.example.com"})
    assert resp.status_code == 302
