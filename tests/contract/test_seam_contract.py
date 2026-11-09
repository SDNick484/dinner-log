"""The seam with Cookbook Collection, checked over HTTP against running copies of both apps.

The contract lives in cookbook-collection's docs/seam.md. These tests need both servers up and are skipped
otherwise, so the default suite stays offline. .github/workflows/contract.yml starts them on loopback with
fresh data directories (the catalog seeds itself from its seed.json) and sets:

  CONTRACT_CATALOG_URL      Cookbook Collection by loopback IP, where LAN_EDIT=1 makes the tests an editor
                            (/admin/api/dinnerlog). It also has READ_TOKEN, and DINNERLOG_API_URL /
                            DINNERLOG_TOKEN pointing back at Dinner Log
  CONTRACT_CATALOG_API_URL  the same catalog under a host name LAN editing doesn't accept: Dinner Log's
                            COOKBOOK_API_URL, so its /admin/api/pick calls work only with the token
  CONTRACT_DINNER_URL       Dinner Log, with COOKBOOK_TOKEN = the catalog's READ_TOKEN and HISTORY_TOKEN =
                            its DINNERLOG_TOKEN
  CONTRACT_DINNER_DATA_DIR  Dinner Log's DATA_DIR, for backfill_uids and a read-only look at meals.db

The tests run in file order against fresh data: later ones count on what earlier ones logged. Nothing here
names a book: the catalog's seed changes on its own main branch, so the tests pick one.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import pytest

CATALOG = os.environ.get("CONTRACT_CATALOG_URL", "").rstrip("/")
CATALOG_API = os.environ.get("CONTRACT_CATALOG_API_URL", "").rstrip("/")
DINNER = os.environ.get("CONTRACT_DINNER_URL", "").rstrip("/")
DINNER_DATA = os.environ.get("CONTRACT_DINNER_DATA_DIR", "")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UID = re.compile(r"[0-9a-f]{32}")

pytestmark = pytest.mark.skipif(not (CATALOG and CATALOG_API and DINNER and DINNER_DATA),
                                reason="needs running apps: set CONTRACT_CATALOG_URL, CONTRACT_CATALOG_API_URL, "
                                       "CONTRACT_DINNER_URL and CONTRACT_DINNER_DATA_DIR "
                                       "(.github/scripts/start-seam.sh does)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def get(url):
    with _opener.open(url, timeout=10) as resp:
        return resp.read().decode("utf-8")


def get_json(url):
    return json.loads(get(url))


def post_dinner(**fields):
    """POST Dinner Log's /new form as the browser would; returns the new meal's id from the redirect."""
    req = urllib.request.Request(f"{DINNER}/new", data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        _opener.open(req, timeout=10)
    except urllib.error.HTTPError as e:   # the redirect, not followed
        assert e.code == 302, f"POST /new: HTTP {e.code}"
        m = re.search(r"/meal/(\d+)", e.headers.get("Location", ""))
        assert m, f"POST /new redirected to {e.headers.get('Location')!r}"
        return int(m.group(1))
    pytest.fail("POST /new didn't redirect to the new dinner")


def meal_row(meal_id):
    con = sqlite3.connect(f"file:{os.path.join(DINNER_DATA, 'meals.db')}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    finally:
        con.close()


@pytest.fixture(scope="module")
def book():
    """A catalog book with at least two indexed recipes that have pages, as the catalog's public API shows it."""
    for b in get_json(f"{CATALOG}/api/books?sort=title")["books"]:
        detail = get_json(f"{CATALOG}/api/books/{b['id']}")
        if sum(1 for r in detail.get("recipes", []) if (r.get("page") or "").strip()) >= 2:
            return detail
    pytest.fail("no book in the catalog has two indexed recipes with pages; has its seed.json changed?")


@pytest.fixture(scope="module")
def picker(book):
    """What Dinner Log's recipe picker gets for that book, through its own /cookbook/recipes."""
    return get_json(f"{DINNER}/cookbook/recipes?" + urllib.parse.urlencode({"title": book["title"]}))


def paged(picker):
    return [r for r in picker["recipes"] if r["page"]]


def test_recipe_picker_returns_catalog_uids(book, picker):
    assert picker["book_id"] == book["id"]
    assert UID.fullmatch(picker["book_uid"] or ""), picker["book_uid"]
    assert picker["book_uid"] == book["uid"]
    assert picker["recipes"], "the picker found no recipes"
    for r in picker["recipes"]:
        assert UID.fullmatch(r["uid"] or ""), f"recipe {r['name']!r} has no uid: {r['uid']!r}"
    assert {r["uid"] for r in picker["recipes"]} == {r["uid"] for r in book["recipes"] if (r["name"] or "").strip()}


def test_dinner_with_uids_resolves_at_recipe_uid_level(book, picker):
    r = paged(picker)[0]
    meal_id = post_dinner(name=r["name"], source_type="cookbook", source_title=picker["book"],
                          source_detail=r["page"], rating="8",
                          cookbook_book_id=picker["book_id"], cookbook_recipe_id=r["id"],
                          cookbook_book_uid=picker["book_uid"], cookbook_recipe_uid=r["uid"])
    row = meal_row(meal_id)
    assert (row["cookbook_book_uid"], row["cookbook_recipe_uid"]) == (picker["book_uid"], r["uid"])

    # force=1: pull /api/history now and rebuild the matches (as an editor, over LAN_EDIT from loopback)
    status = get_json(f"{CATALOG}/admin/api/dinnerlog?force=1")
    assert status["error"] is None, status["error"]
    assert status["available"] is True, status
    resolved = status["resolved"]
    assert resolved["recipe_uid"] >= 1, resolved
    assert resolved.get("uid_missed", 0) == 0, resolved
    assert resolved.get("int", 0) == 0 and resolved.get("text", 0) == 0, resolved


def test_pick_for_tonight_comes_from_the_catalog():
    # Where Dinner Log calls from, the catalog's picks need READ_TOKEN: LAN editing doesn't let it in.
    with pytest.raises(urllib.error.HTTPError) as no_token:
        get(f"{CATALOG_API}/admin/api/pick?mode=new")
    assert no_token.value.code == 401
    # "Try new" draws only from that route, sent with COOKBOOK_TOKEN; without the catalog it shows no card.
    # Only catalog picks carry uids into the "Log tonight" link.
    html = get(f"{DINNER}/?suggest=1&mode=new")
    assert "isn't connected right now" not in html, "Dinner Log couldn't reach the catalog's picks"
    book_uid = re.search(r"cookbook_book_uid=([0-9a-f]{32})", html)
    recipe_uid = re.search(r"cookbook_recipe_uid=([0-9a-f]{32})", html)
    assert book_uid and recipe_uid, "no catalog pick on the page"
    pick_book = get_json(f"{CATALOG}/api/books/by-uid/{book_uid.group(1)}")
    assert recipe_uid.group(1) in {r["uid"] for r in pick_book["recipes"]}


def test_backfill_uids_dry_run_against_the_catalog(picker):
    r = paged(picker)[1]
    meal_id = post_dinner(name=r["name"], source_type="cookbook", source_title=picker["book"],
                          source_detail=r["page"], cookbook_book_id=picker["book_id"], cookbook_recipe_id=r["id"])
    assert meal_row(meal_id)["cookbook_recipe_uid"] is None

    env = {**os.environ, "DATA_DIR": DINNER_DATA, "COOKBOOK_API_URL": CATALOG}
    out = subprocess.run([sys.executable, "-m", "backfill_uids", "--dry-run"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    line = next((ln for ln in out.stdout.splitlines() if re.match(rf"meal\s+{meal_id}\s", ln)), None)
    assert line, out.stdout
    assert f"would set book_uid={picker['book_uid']}" in line and f"recipe_uid={r['uid']}" in line, line
    assert "(dry run: nothing written)" in out.stdout
    row = meal_row(meal_id)
    assert row["cookbook_book_uid"] is None and row["cookbook_recipe_uid"] is None, "the dry run wrote uids"
