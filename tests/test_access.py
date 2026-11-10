"""Cloudflare Access verification inside the app (CF_TEAM_DOMAIN + CF_AUD), its exemptions and LAN_ACCESS.

Tokens are signed with a throwaway RSA key. The JWKS fetch is stubbed at PyJWKClient.fetch_data, so the
app's own key cache and its refetch limits are what's under test. Flask's test client sets the socket
peer (REMOTE_ADDR) and Host directly: what a LAN client or a DNS-rebinding page controls.
"""
import time

import pytest

from test_app import load_app, text

jwt = pytest.importorskip("jwt")  # PyJWT; the Alpine installer adds py3-jwt, Docker gets it from requirements.txt
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
from jwt.algorithms import RSAAlgorithm  # noqa: E402

TEAM = "team.example.com"
AUD = "aud-0123456789abcdef"
LAN_PEER = {"REMOTE_ADDR": "192.168.1.20"}
LAN_URL = "http://192.168.1.5:8000"          # the server's own LAN address, typed in the browser
CF = {"Cf-Ray": "abc123-LAX", "Cf-Connecting-Ip": "203.0.113.9"}


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = new_key()


def jwk(key, kid):
    d = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**d, "kid": kid, "alg": "RS256", "use": "sig"}


class FakeCerts:
    """Stands in for https://<team>/cdn-cgi/access/certs: counts fetches, can fail or rotate."""
    def __init__(self, *keys):
        self.keys = list(keys)   # (private key, kid)
        self.calls = 0
        self.fail = False

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise OSError("certs endpoint down")
        return {"keys": [jwk(k, kid) for k, kid in self.keys]}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def token(key=KEY, kid="k1", aud=AUD, iss=f"https://{TEAM}", exp=3600, iat=0, email="cook@example.com",
          alg="RS256", **extra):
    now = int(time.time())
    claims = {"aud": aud, "iss": iss, "exp": now + exp, "iat": now + iat, "email": email, **extra}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid} if kid else None)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("CF_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_AUD", AUD)
    for k in ("LAN_ACCESS", "LAN_CIDRS", "LAN_HOSTS", "HISTORY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def boot(tmp_path, monkeypatch, certs=None):
    a = load_app(tmp_path)
    certs = certs or FakeCerts((KEY, "k1"))
    monkeypatch.setattr(a.ACCESS_KEYS.client, "fetch_data", certs)
    clock = Clock()
    monkeypatch.setattr(a.ACCESS_KEYS, "clock", clock)
    return a, a.app.test_client(), certs, clock


@pytest.fixture()
def app_on(tmp_path, env):
    return boot(tmp_path, env)


def get(c, path="/", tok=None, base=LAN_URL, peer=LAN_PEER, **headers):
    if tok is not None:
        headers["Cf-Access-Jwt-Assertion"] = tok
    return c.get(path, base_url=base, environ_base=peer, headers=headers)


def via_cf(c, tok, path="/"):
    return get(c, path, tok=tok, base="https://dinners.example.com", peer={"REMOTE_ADDR": "127.0.0.1"}, **CF)


# ------------------------------------------------------------------ off by default
def test_without_settings_nothing_changes(tmp_path, monkeypatch):
    for k in ("CF_TEAM_DOMAIN", "CF_AUD", "LAN_ACCESS"):
        monkeypatch.delenv(k, raising=False)
    a = load_app(tmp_path)
    c = a.app.test_client()
    assert a.ACCESS_ON is False
    assert c.get("/").status_code == 200
    assert c.get("/static/manifest.json").status_code == 200
    assert c.post("/new", data={"name": "Tacos"}).status_code == 302


@pytest.mark.parametrize("only", ["CF_TEAM_DOMAIN", "CF_AUD"])
def test_half_configured_refuses_to_start(tmp_path, monkeypatch, only):
    for k in ("CF_TEAM_DOMAIN", "CF_AUD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(only, "x")
    with pytest.raises(RuntimeError, match="both"):
        load_app(tmp_path)


def test_team_domain_is_normalized(tmp_path, env):
    env.setenv("CF_TEAM_DOMAIN", f" 'https://{TEAM}/' ")
    a = load_app(tmp_path)
    assert a.CF_TEAM_DOMAIN == TEAM


# ------------------------------------------------------------------ tokens
def test_valid_token(app_on):
    a, c, certs, _ = app_on
    r = via_cf(c, token())
    assert r.status_code == 200 and "Dinner Log" in text(r)
    assert certs.calls == 1
    assert via_cf(c, token()).status_code == 200 and certs.calls == 1      # cached: no second fetch


def test_verified_email_is_kept_for_later(app_on):
    a, *_ = app_on
    with a.app.test_request_context("/", headers={"Cf-Access-Jwt-Assertion": token(email="Cook@Example.com"),
                                                  **CF}):
        assert a.require_access() is None
        assert a.g.access_email == "cook@example.com"


def test_service_token_without_email_is_accepted(app_on):
    _, c, _, _ = app_on
    assert via_cf(c, token(email=None, common_name="abc.access")).status_code == 200


@pytest.mark.parametrize("bad", [
    dict(exp=-3600),                                  # expired
    dict(aud="another-app"),                          # another Access application
    dict(iss="https://evil.example.com"),             # another team
    dict(key=new_key()),                              # signed by someone else, claiming our kid
    dict(iat=3600),                                   # issued an hour in the future
], ids=["expired", "audience", "issuer", "signature", "future-iat"])
def test_bad_tokens_are_refused(app_on, bad):
    _, c, _, _ = app_on
    r = via_cf(c, token(**bad))
    assert r.status_code == 403


def test_small_clock_skew_is_tolerated(app_on):
    _, c, _, _ = app_on
    assert via_cf(c, token(iat=20)).status_code == 200


@pytest.mark.parametrize("raw", ["garbage", "a.b.c", "", " "])
def test_malformed_tokens_are_refused(app_on, raw):
    _, c, certs, _ = app_on
    assert via_cf(c, raw).status_code == 403
    assert certs.calls == 0                           # rejected before any key lookup


def test_hs256_token_is_refused_without_a_fetch(app_on):
    _, c, certs, _ = app_on
    hs = jwt.encode({"aud": AUD, "iss": f"https://{TEAM}", "exp": int(time.time()) + 60},
                    "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    assert via_cf(c, hs).status_code == 403 and certs.calls == 0


def test_alg_none_is_refused(app_on):
    _, c, certs, _ = app_on
    none = jwt.encode({"aud": AUD, "iss": f"https://{TEAM}", "exp": int(time.time()) + 60}, None,
                      algorithm="none", headers={"kid": "k1"})
    assert via_cf(c, none).status_code == 403 and certs.calls == 0


def test_token_without_kid_is_refused(app_on):
    _, c, certs, _ = app_on
    assert via_cf(c, token(kid=None)).status_code == 403 and certs.calls == 0


# ------------------------------------------------------------------ key cache (audit M-2)
def test_unknown_kid_refetch_is_rate_limited(app_on):
    _, c, certs, clock = app_on
    assert via_cf(c, token()).status_code == 200 and certs.calls == 1
    clock.t += 61
    for _ in range(5):                                 # junk kids: one refetch, then nothing for a minute
        assert via_cf(c, token(kid="junk")).status_code == 403
    assert certs.calls == 2
    clock.t += 30
    assert via_cf(c, token(kid="junk2")).status_code == 403 and certs.calls == 2
    clock.t += 31
    assert via_cf(c, token(kid="junk3")).status_code == 403 and certs.calls == 3
    assert via_cf(c, token()).status_code == 200 and certs.calls == 3        # good kid never needed a fetch


def test_rotated_key_is_picked_up(app_on):
    _, c, certs, clock = app_on
    assert via_cf(c, token()).status_code == 200
    k2 = new_key()
    certs.keys.append((k2, "k2"))
    clock.t += 61
    assert via_cf(c, token(key=k2, kid="k2")).status_code == 200 and certs.calls == 2


def test_fetch_failure_keeps_cached_keys_and_backs_off(app_on):
    _, c, certs, clock = app_on
    assert via_cf(c, token()).status_code == 200
    certs.fail = True
    clock.t += 3601                                    # keys are stale: the next request tries a refresh
    assert via_cf(c, token()).status_code == 200 and certs.calls == 2       # refresh failed; old key still good
    for _ in range(3):
        assert via_cf(c, token(kid="junk")).status_code == 403
    assert certs.calls == 2                            # backing off after the failure, not hammering
    clock.t += 61
    assert via_cf(c, token()).status_code == 200 and certs.calls == 3


def test_first_fetch_failure_refuses_and_backs_off(tmp_path, env):
    certs = FakeCerts((KEY, "k1"))
    certs.fail = True
    _, c, certs, clock = boot(tmp_path, env, certs)
    for _ in range(3):
        assert via_cf(c, token()).status_code == 403
    assert certs.calls == 1
    certs.fail = False
    clock.t += 61
    assert via_cf(c, token()).status_code == 200


def test_stale_keys_are_refreshed_and_dropped_keys_stop_working(app_on):
    _, c, certs, clock = app_on
    assert via_cf(c, token()).status_code == 200
    k2 = new_key()
    certs.keys = [(k2, "k2")]                          # k1 withdrawn
    clock.t += 3601
    assert via_cf(c, token()).status_code == 403 and certs.calls == 2


# ------------------------------------------------------------------ refusal page
def test_refusal_page_reveals_nothing(app_on):
    _, c, _, _ = app_on
    for r in (via_cf(c, token(aud="another-app")), get(c), via_cf(c, "garbage")):
        body = text(r)
        assert r.status_code == 403 and r.headers["Cache-Control"] == "no-store"
        assert AUD not in body and TEAM not in body and "another-app" not in body
        assert "Sign-in required" in body


def test_unknown_paths_get_403_not_404(app_on):
    _, c, _, _ = app_on
    assert get(c, "/no-such-page").status_code == 403
    assert via_cf(c, token(), "/no-such-page").status_code == 404


# ------------------------------------------------------------------ everything else needs the token
@pytest.mark.parametrize("path", ["/", "/new", "/sw.js", "/static/manifest.json", "/static/icon-192.png",
                                  "/cookbook/recipes?title=x", "/photos/x.jpg", "/meal/1", "/last-time?name=Gumbo"])
def test_pages_static_and_pwa_files_need_the_token(app_on, path):
    _, c, _, _ = app_on
    assert get(c, path).status_code == 403
    assert via_cf(c, token(), path).status_code != 403


def test_writes_need_the_token(app_on):
    _, c, _, _ = app_on
    r = c.post("/new", data={"name": "Sneaky"}, base_url=LAN_URL, environ_base=LAN_PEER)
    assert r.status_code == 403
    r = c.post("/new", data={"name": "Tacos"}, base_url="https://dinners.example.com",
               environ_base={"REMOTE_ADDR": "127.0.0.1"},
               headers={**CF, "Cf-Access-Jwt-Assertion": token(), "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 302


def test_cross_site_check_still_applies_with_a_token(app_on):
    _, c, _, _ = app_on
    r = c.post("/new", data={"name": "Evil"}, base_url="https://dinners.example.com",
               headers={**CF, "Cf-Access-Jwt-Assertion": token(), "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


# ------------------------------------------------------------------ exemptions
def test_healthz_is_open(app_on):
    _, c, certs, _ = app_on
    r = get(c, "/healthz", peer={"REMOTE_ADDR": "203.0.113.50"}, base="http://anything.example")
    assert r.status_code == 200 and text(r) == "ok\n" and certs.calls == 0


def test_history_works_by_bearer_only(tmp_path, env):
    env.setenv("HISTORY_TOKEN", "h" * 64)
    _, c, _, _ = boot(tmp_path, env)
    ok = get(c, "/api/history", peer={"REMOTE_ADDR": "127.0.0.1"}, base="http://127.0.0.1:8000",
             Authorization="Bearer " + "h" * 64)
    assert ok.status_code == 200 and ok.json["version"] == 1
    assert get(c, "/api/history").status_code == 401
    assert get(c, "/api/history", Authorization="Bearer wrong").status_code == 401
    assert via_cf(c, token(), "/api/history").status_code == 401          # an Access login isn't the token


def test_history_still_404_without_its_token(app_on):
    _, c, _, _ = app_on
    assert get(c, "/api/history").status_code == 404


def test_exemptions_are_explicit(app_on):
    """Every exempt route is listed here on purpose. A new @public_route means editing this test; every
    @bearer_route must refuse a request that carries no Authorization header."""
    a, c, _, _ = app_on
    exempt = {ep: getattr(fn, "access_exempt", None) for ep, fn in a.app.view_functions.items()
              if getattr(fn, "access_exempt", None)}
    assert {ep for ep, kind in exempt.items() if kind == "public"} == {"healthz"}
    assert set(exempt.values()) <= {"public", "bearer"}
    bearer = [ep for ep, kind in exempt.items() if kind == "bearer"]
    assert bearer == ["history"]
    a.HISTORY_TOKEN = "h" * 64                         # set, so the route exists and must check it
    for rule in a.app.url_map.iter_rules():
        if rule.endpoint in bearer:
            assert get(c, rule.rule).status_code in (401, 404), rule.rule


def test_share_target_is_gated(app_on):
    """The manifest's share_target posts to /share; it's a write route like /new, never exempt."""
    a, c, _, _ = app_on
    r = c.post("/share", data={"title": "x"}, base_url=LAN_URL, environ_base=LAN_PEER)
    assert r.status_code == 403
    ok = c.post("/share", data={"title": "x"}, base_url="https://dinners.example.com",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
                headers={**CF, "Cf-Access-Jwt-Assertion": token(), "Sec-Fetch-Site": "none"})
    assert ok.status_code == 200


# ------------------------------------------------------------------ LAN_ACCESS
@pytest.fixture()
def lan_on(tmp_path, env):
    env.setenv("LAN_ACCESS", "1")
    return boot(tmp_path, env)


def test_no_token_without_lan_access_is_refused(app_on):
    _, c, _, _ = app_on
    assert get(c).status_code == 403


def test_lan_peer_with_ip_host(lan_on):
    _, c, certs, _ = lan_on
    assert get(c).status_code == 200 and certs.calls == 0
    assert c.post("/new", data={"name": "Tacos"}, base_url=LAN_URL, environ_base=LAN_PEER).status_code == 302


@pytest.mark.parametrize("peer", ["127.0.0.1", "10.1.2.3", "172.20.0.4", "::1", "fd00::5", "fe80::1%eth0",
                                  "::ffff:192.168.1.20"])
def test_lan_peers_inside_default_cidrs(lan_on, peer):
    _, c, _, _ = lan_on
    assert get(c, peer={"REMOTE_ADDR": peer}).status_code == 200


@pytest.mark.parametrize("peer", ["203.0.113.9", "8.8.8.8", "2001:db8::1", "", "not-an-ip"])
def test_peers_outside_lan_cidrs_refused(lan_on, peer):
    _, c, _, _ = lan_on
    assert get(c, peer={"REMOTE_ADDR": peer}).status_code == 403


@pytest.mark.parametrize("base", ["http://attacker.example:8000", "http://dinners.example.com",
                                  "http://dinner-log.lan:8000"])
def test_name_hosts_refused_dns_rebinding(lan_on, base):
    _, c, _, _ = lan_on
    assert get(c, base=base).status_code == 403


def test_lan_hosts_and_localhost(tmp_path, env):
    env.setenv("LAN_ACCESS", "1")
    env.setenv("LAN_HOSTS", "Dinner-Log.lan, other.lan")
    _, c, _, _ = boot(tmp_path, env)
    assert get(c, base="http://dinner-log.lan:8000").status_code == 200
    assert get(c, base="http://localhost:8000", peer={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert get(c, base="http://[::1]:8000", peer={"REMOTE_ADDR": "::1"}).status_code == 200
    assert get(c, base="http://attacker.example").status_code == 403


def test_lan_cidrs_narrow_the_lan(tmp_path, env):
    env.setenv("LAN_ACCESS", "1")
    env.setenv("LAN_CIDRS", "192.168.1.0/24")
    _, c, _, _ = boot(tmp_path, env)
    assert get(c).status_code == 200
    assert get(c, peer={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403
    assert get(c, peer={"REMOTE_ADDR": "127.0.0.1"}).status_code == 403


def test_cloudflare_requests_never_take_the_lan_path(lan_on):
    """cloudflared on this host connects from 127.0.0.1: a path Access let through without a token must
    not count as LAN, even if the tunnel rewrote Host to an IP address."""
    _, c, _, _ = lan_on
    for h in ({"Cf-Ray": "abc"}, {"Cf-Connecting-Ip": "203.0.113.9"}):
        assert get(c, base="http://127.0.0.1:8000", peer={"REMOTE_ADDR": "127.0.0.1"}, **h).status_code == 403


def test_bad_token_never_falls_back_to_lan(lan_on):
    _, c, _, _ = lan_on
    assert get(c, tok="garbage").status_code == 403
    assert get(c, tok=token(aud="another-app")).status_code == 403
    assert get(c).status_code == 200                   # the same request without the bad token is fine


@pytest.mark.parametrize("raw,on", [("1", True), ('"1"', True), (" 1 ", True), ("0", False), ("", False),
                                    ("yes", False), ("true", False)])
def test_lan_access_flag(tmp_path, env, raw, on):
    env.setenv("LAN_ACCESS", raw)
    assert load_app(tmp_path).LAN_ACCESS is on
