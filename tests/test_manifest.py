"""The web app manifest: valid, installable, and a share target that matches the app's routes and form."""
import json
import os
import re

import pytest

from test_app import ROOT, load_app

MANIFEST = os.path.join(ROOT, "static", "manifest.json")
SW = os.path.join(ROOT, "static", "sw.js")
FORM = os.path.join(ROOT, "templates", "form.html")


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def test_installable_basics(manifest):
    assert manifest["name"] and manifest["short_name"]
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"
    sizes = {i["sizes"] for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes                     # what Chrome needs to offer "Install"
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/")
        assert os.path.isfile(os.path.join(ROOT, icon["src"].lstrip("/"))), icon["src"]


def test_share_target_shape(manifest):
    st = manifest["share_target"]
    assert st["method"] == "POST" and st["enctype"] == "multipart/form-data"   # files need both
    p = st["params"]
    assert (p["title"], p["text"], p["url"]) == ("title", "text", "url")
    [files] = p["files"]
    assert "image/*" in files["accept"] and ".heic" in files["accept"]
    assert all(a == "image/*" or re.fullmatch(r"\.[a-z]+", a) for a in files["accept"])


def test_share_target_matches_the_server_and_the_form(manifest, tmp_path):
    st = manifest["share_target"]
    a = load_app(tmp_path)
    rules = {r.rule: r for r in a.app.url_map.iter_rules()}
    assert "POST" in rules[st["action"]].methods
    # The fallback reads the same field names the manifest sends, and the same name the form uploads.
    files_name = st["params"]["files"][0]["name"]
    with open(FORM, encoding="utf-8") as f:
        assert f'name="{files_name}"' in f.read()
    c = a.app.test_client()
    r = c.post(st["action"], data={"title": "Pho", "text": "https://example.com/pho"},
               content_type=st["enctype"])
    assert r.status_code == 200 and 'value="Pho"' in r.get_data(as_text=True)


def test_service_worker_intercepts_the_share_action(manifest):
    """static/sw.js has its own copy of the action path and the files field; keep them in step."""
    with open(SW, encoding="utf-8") as f:
        sw = f.read()
    assert re.search(r"SHARE_PATH\s*=\s*'" + re.escape(manifest["share_target"]["action"]) + "'", sw)
    assert re.search(r"getAll\('" + manifest["share_target"]["params"]["files"][0]["name"] + "'\)", sw)


def test_manifest_served(tmp_path):
    c = load_app(tmp_path).app.test_client()
    r = c.get("/static/manifest.json")
    assert r.status_code == 200 and json.loads(r.get_data())["share_target"]["action"] == "/share"
