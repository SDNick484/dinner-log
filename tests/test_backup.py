"""backup.py: consistent SQLite snapshots, retention, the copy to BACKUP_TARGET, and the skip when
Cookbook Collection's app-backup already covers the host."""
import datetime
import gzip
import importlib
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

from test_app import ROOT, load_app

TODAY = datetime.date(2026, 11, 9)


@pytest.fixture()
def backup():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.modules.pop("backup", None)
    return importlib.import_module("backup")


@pytest.fixture()
def data(tmp_path):
    """A real Dinner Log data dir: schema from the app, two dinners, one photo."""
    d = tmp_path / "data"
    d.mkdir()
    a = load_app(d)
    con = sqlite3.connect(a.DB_PATH)
    con.execute("INSERT INTO meals (day, name, source_type, rating) VALUES ('2026-11-01', 'Soup', 'original', 7)")
    con.execute("INSERT INTO meals (day, name, source_type, source_title, source_detail) "
                "VALUES ('2026-11-02', 'Pad thai', 'cookbook', 'Thai Food', 'p. 88')")
    con.execute("INSERT INTO photos (meal_id, filename) VALUES (2, 'a1.jpg')")
    con.commit()
    con.close()
    (d / "photos" / "a1.jpg").write_bytes(b"jpeg one")
    return d


def rows(db_path):
    con = sqlite3.connect(db_path)
    try:
        return (con.execute("SELECT * FROM meals ORDER BY id").fetchall(),
                con.execute("SELECT * FROM photos ORDER BY id").fetchall())
    finally:
        con.close()


def restore(gz, dest):
    with gzip.open(gz, "rb") as f:
        dest.write_bytes(f.read())
    return dest


def run(backup, data, out, target=None, keep=14, today=TODAY, skip_if=()):
    return backup.run(data_dir=str(data), backup_dir=str(out), keep_days=keep,
                      target=str(target) if target else "", today=today, skip_if=skip_if)


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_snapshot_restores_to_the_same_rows(backup, data, tmp_path):
    out = tmp_path / "backups"
    assert run(backup, data, out) == 0
    gz = out / "2026-11-09" / "meals.db.gz"
    got = restore(gz, tmp_path / "restored.db")
    assert rows(got) == rows(data / "meals.db")
    assert len(rows(got)[0]) == 2
    assert mode(out) == 0o700 and mode(gz.parent) == 0o700 and mode(gz) == 0o600
    assert os.listdir(gz.parent) == ["meals.db.gz"]                     # no temp files left behind


def test_uncommitted_writes_are_not_in_the_snapshot(backup, data, tmp_path):
    writer = sqlite3.connect(data / "meals.db")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO meals (day, name) VALUES ('2026-11-03', 'Half-written')")
    try:
        assert run(backup, data, tmp_path / "b") == 0
    finally:
        writer.rollback()
        writer.close()
    names = [r[2] for r in rows(restore(tmp_path / "b" / "2026-11-09" / "meals.db.gz", tmp_path / "r.db"))[0]]
    assert names == ["Soup", "Pad thai"]


def test_failed_integrity_check_leaves_no_snapshot(backup, data, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "integrity", lambda path: "*** in database main ***\nPage 2: btree problem")
    out = tmp_path / "b"
    assert run(backup, data, out) == 1
    assert not (out / "2026-11-09" / "meals.db.gz").exists()
    assert os.listdir(out / "2026-11-09") == []                         # no temp files either


def test_missing_database_is_an_error_not_an_empty_backup(backup, tmp_path):
    empty = tmp_path / "data"
    empty.mkdir()
    assert run(backup, empty, tmp_path / "b") == 1
    assert not (empty / "meals.db").exists()                            # sqlite didn't create one either


def test_a_second_run_the_same_day_replaces_the_snapshot(backup, data, tmp_path):
    out = tmp_path / "b"
    assert run(backup, data, out) == 0
    con = sqlite3.connect(data / "meals.db")
    con.execute("INSERT INTO meals (day, name) VALUES ('2026-11-09', 'Tacos')")
    con.commit()
    con.close()
    assert run(backup, data, out) == 0
    assert len(rows(restore(out / "2026-11-09" / "meals.db.gz", tmp_path / "r.db"))[0]) == 3


def test_retention_keeps_recent_days_and_foreign_files(backup, data, tmp_path):
    out = tmp_path / "b"
    for day in ("2026-10-25", "2026-10-26", "2026-11-08"):              # 15, 14 and 1 days old
        (out / day).mkdir(parents=True)
        (out / day / "meals.db.gz").write_bytes(b"old")
    (out / "2026-10-01").mkdir()
    (out / "2026-10-01" / "notes.txt").write_text("mine, not the script's")
    (out / "keep-me").mkdir()
    (out / "README").write_text("x")
    assert run(backup, data, out, keep=14) == 0
    left = sorted(os.listdir(out))
    assert left == ["2026-10-01", "2026-10-26", "2026-11-08", "2026-11-09", "README", "keep-me"]
    assert os.listdir(out / "2026-10-01") == ["notes.txt"]              # only its own file would go


def test_target_gets_snapshot_and_new_photos_only(backup, data, tmp_path):
    target = tmp_path / "nas"
    target.mkdir()
    assert run(backup, data, tmp_path / "b", target=target) == 0
    snap = target / "db" / "2026-11-09" / "dinner-log-meals.db.gz"
    assert rows(restore(snap, tmp_path / "r.db")) == rows(data / "meals.db")
    photos = target / "files" / "dinner-log" / "photos"
    assert (photos / "a1.jpg").read_bytes() == b"jpeg one"

    (photos / "a1.jpg").write_bytes(b"already there")                  # write-once: never overwritten
    (data / "photos" / "b2.jpg").write_bytes(b"jpeg two")
    os.remove(data / "photos" / "a1.jpg")                               # deleted in the app: kept in the backup
    assert backup.copy_new_files(str(data / "photos"), str(photos)) == 1
    assert (photos / "a1.jpg").read_bytes() == b"already there"
    assert (photos / "b2.jpg").read_bytes() == b"jpeg two"
    assert backup.copy_new_files(str(data / "photos"), str(photos)) == 0
    assert not [n for n in os.listdir(photos) if n.startswith(".")]


def test_target_retention_leaves_app_backup_files_alone(backup, data, tmp_path):
    target = tmp_path / "nas"
    old = target / "db" / "2026-10-01"
    old.mkdir(parents=True)
    (old / "dinner-log-meals.db.gz").write_bytes(b"ours")
    (old / "cookbooks-cookbooks.db.gz").write_bytes(b"app-backup's")
    gone = target / "db" / "2026-10-02"
    gone.mkdir()
    (gone / "dinner-log-meals.db.gz").write_bytes(b"ours")
    assert run(backup, data, tmp_path / "b", target=target) == 0
    assert os.listdir(old) == ["cookbooks-cookbooks.db.gz"]
    assert not gone.exists()


def test_unwritable_target_fails_but_local_snapshot_is_kept(backup, data, tmp_path):
    out = tmp_path / "b"
    assert run(backup, data, out, target=tmp_path / "not-mounted") == 1
    assert (out / "2026-11-09" / "meals.db.gz").exists()


def test_skip_when_app_backup_covers_the_host(backup, data, tmp_path):
    job = tmp_path / "periodic" / "app-backup"
    out = tmp_path / "b"
    assert run(backup, data, out, skip_if=[str(job)]) == 0              # not installed: runs
    assert (out / "2026-11-09").exists()
    job.parent.mkdir()
    job.write_text("#!/bin/sh\n")
    out2 = tmp_path / "b2"
    assert run(backup, data, out2, skip_if=[str(job)]) == 0             # installed: nothing written
    assert not out2.exists()


def test_dangling_app_backup_link_does_not_skip(backup, data, tmp_path):
    """A link left behind after its script was removed can't run, so Dinner Log backs itself up."""
    job = tmp_path / "app-backup"
    os.symlink(tmp_path / "gone", job)
    assert run(backup, data, tmp_path / "b", skip_if=[str(job)]) == 0
    assert (tmp_path / "b" / "2026-11-09" / "meals.db.gz").exists()


def test_command_line_uses_environment(data, tmp_path):
    out = tmp_path / "b"
    env = {**os.environ, "DATA_DIR": str(data), "BACKUP_DIR": str(out), "BACKUP_KEEP_DAYS": "3",
           "BACKUP_TARGET": ""}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "backup.py")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    (day,) = os.listdir(out)
    assert (out / day / "meals.db.gz").exists() and "ok" in r.stdout


def test_backup_dir_defaults_inside_data_dir(data):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BACKUP_")}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "backup.py")], env={**env, "DATA_DIR": str(data)},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.listdir(data / "backups")


@pytest.mark.parametrize("raw", ["0", "-1", "x"])
def test_bad_keep_days_is_refused(data, tmp_path, raw):
    env = {**os.environ, "DATA_DIR": str(data), "BACKUP_DIR": str(tmp_path / "b"), "BACKUP_KEEP_DAYS": raw}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "backup.py")], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "BACKUP_KEEP_DAYS" in r.stderr
