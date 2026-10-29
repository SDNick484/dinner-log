"""deploy/build.sh: the one-file Alpine installer (shell header + base64 tarball of the app)."""
import base64
import io
import os
import shutil
import subprocess
import tarfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("sh", "tar", "base64")),
                                reason="needs sh, tar and base64")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist") / "setup-dinner-log.sh"
    r = subprocess.run(["sh", os.path.join(ROOT, "deploy", "build.sh")], env={**os.environ, "OUT": str(out)},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def payload(path):
    text = path.read_text()
    head, _, b64 = text.partition("\n__PAYLOAD_BELOW__\n")
    return head, tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64)), mode="r:gz")


def test_output_is_valid_sh_and_executable(built):
    assert subprocess.run(["sh", "-n", str(built)]).returncode == 0
    assert os.access(built, os.X_OK)


def test_payload_is_the_app_only(built):
    _, tar = payload(built)
    names = tar.getnames()
    top = {n.split("/")[1] for n in names if n.count("/") >= 1}
    assert {n.split("/")[0] for n in names} == {"dinner-log"}
    assert top == {"app.py", "templates", "static", "deploy"}
    assert "dinner-log/deploy/install-alpine.sh" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") or "/data/" in n for n in names)
    assert all(m.uid == 0 and m.gid == 0 for m in tar.getmembers())


def test_header_runs_the_alpine_installer(built):
    head, _ = payload(built)
    assert head.startswith("#!/bin/sh")
    assert 'sh "$TMP/dinner-log/deploy/install-alpine.sh"' in head
    assert "trap" in head and "exit 0" in head


def test_header_unpacks_and_runs_from_any_cwd(built, tmp_path):
    """Run the real header with the install step swapped for a probe, from another folder, by relative path."""
    probe = tmp_path / "probe" / "setup.sh"
    probe.parent.mkdir()
    text = built.read_text().replace('sh "$TMP/dinner-log/deploy/install-alpine.sh"',
                                     'echo "TMP=$TMP"; ls "$TMP/dinner-log"; test -f "$TMP/dinner-log/deploy/install-alpine.sh"', 1)
    assert text != built.read_text()
    probe.write_text(text)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run(["sh", "../probe/setup.sh"], cwd=elsewhere, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    tmp = r.stdout.split("TMP=", 1)[1].split("\n", 1)[0]
    assert set(r.stdout.split("\n", 1)[1].split()) == {"app.py", "templates", "static", "deploy"}
    assert not os.path.exists(tmp)                                       # cleaned up on exit
