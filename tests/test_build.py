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
