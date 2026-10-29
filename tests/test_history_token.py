"""deploy/history-token.sh: give HISTORY_TOKEN a value on install when the config has none, never replace one."""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "deploy", "history-token.sh")
COMMENT = "# Token Cookbook Collection uses to read cooking history (optional)"

pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


def run(conf):
    r = subprocess.run(["sh", SCRIPT, str(conf)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def token_lines(conf):
    return [l for l in conf.read_text().splitlines() if re.match(r"^(export )?HISTORY_TOKEN=", l)]


def written_token(conf):
    (line,) = token_lines(conf)
    m = re.fullmatch(r"export HISTORY_TOKEN=([0-9a-f]{64})", line)
    assert m, line
    return m.group(1)


def test_missing_setting_gets_a_token(tmp_path):
    conf = tmp_path / "dinner-log"
    conf.write_text("DINNER_PORT=8000\n")
    out = run(conf)
    tok = written_token(conf)
    assert "generated" in out and tok not in out                  # never printed
    assert conf.read_text().startswith("DINNER_PORT=8000\n") and COMMENT in conf.read_text()


@pytest.mark.parametrize("empty", ['export HISTORY_TOKEN=', 'export HISTORY_TOKEN=""', "export HISTORY_TOKEN=''",
                                   "HISTORY_TOKEN=", 'export HISTORY_TOKEN="  "'])
def test_empty_setting_is_filled_in_place(tmp_path, empty):
    conf = tmp_path / "dinner-log"
    conf.write_text(f"DINNER_PORT=8000\n{COMMENT}\n{empty}\nexport APP_TZ=UTC\n")
    assert "generated" in run(conf)
    tok = written_token(conf)
    assert conf.read_text() == f"DINNER_PORT=8000\n{COMMENT}\nexport HISTORY_TOKEN={tok}\nexport APP_TZ=UTC\n"


@pytest.mark.parametrize("existing", ['export HISTORY_TOKEN=my-own-secret', 'export HISTORY_TOKEN="my own secret"',
                                      "HISTORY_TOKEN=abc"])
def test_existing_token_is_kept(tmp_path, existing):
    conf = tmp_path / "dinner-log"
    before = f"{COMMENT}\n{existing}\n"
    conf.write_text(before)
    assert "kept" in run(conf)
    assert conf.read_text() == before


def test_rerun_keeps_the_generated_token(tmp_path):
    conf = tmp_path / "dinner-log"
    conf.write_text("")
    run(conf)
    first = written_token(conf)
    assert "kept" in run(conf)
    assert written_token(conf) == first


def test_tokens_differ_between_installs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("")
    b.write_text("")
    run(a)
    run(b)
    assert written_token(a) != written_token(b)


def test_installer_calls_it():
    src = open(os.path.join(ROOT, "deploy", "install-alpine.sh")).read()
    assert 'sh "$SRC/deploy/history-token.sh" "$CONF"' in src
    assert 'set_conf "export HISTORY_TOKEN"' not in src
