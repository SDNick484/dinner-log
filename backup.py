"""Back up Dinner Log: a consistent snapshot of the database, kept for a number of days, and optionally
copied (with new photo files) to other storage.

    python3 backup.py [--skip-if PATH ...]

Settings come from the environment (the Alpine cron job sources /etc/conf.d/dinner-log first):

  DATA_DIR          where meals.db and photos/ live (default ./data, as for the app)
  BACKUP_DIR        where snapshots go (default DATA_DIR/backups)
  BACKUP_KEEP_DAYS  days of snapshots to keep (default 14)
  BACKUP_TARGET     optional folder on other storage, e.g. a NAS mount; gets the snapshots and photos

Layout, the same as Cookbook Collection's app-backup uses on BACKUP_TARGET, so the two can share one:

  BACKUP_DIR/2026-11-09/meals.db.gz
  BACKUP_TARGET/db/2026-11-09/dinner-log-meals.db.gz
  BACKUP_TARGET/files/dinner-log/photos/<name>.jpg     new files only; nothing there is ever deleted

The snapshot uses SQLite's online backup API (no sqlite3 command needed, so it also runs in the Docker
image), must pass PRAGMA integrity_check, and is gzipped and renamed into place only when complete.
--skip-if PATH exits without doing anything when PATH exists (e.g. the app-backup job that already
covers this host). Exit status: 0 done or skipped, 1 something failed, 2 bad settings.
Standard library only; it doesn't import the app.
"""
import argparse
import datetime
import gzip
import os
import re
import shutil
import sqlite3
import sys

DB_NAME = "meals.db"
TARGET_DB_NAME = "dinner-log-meals.db.gz"
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class BackupError(Exception):
    pass


def log(msg):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} dinner-log backup: {msg}", flush=True)


def private_dir(path):
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o700)


def integrity(path):
    """PRAGMA integrity_check's answer: "ok" when the file is sound."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return "\n".join(r[0] for r in con.execute("PRAGMA integrity_check"))
    finally:
        con.close()


def _remove(*paths):
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def snapshot(db_path, out_gz):
    """Write a gzipped, integrity-checked copy of db_path to out_gz (0600), atomically."""
    if not os.path.isfile(db_path):
        raise BackupError(f"no database at {db_path}")   # don't let sqlite3.connect create an empty one
    tmp_db = f"{os.path.dirname(out_gz)}/.{DB_NAME}.{os.getpid()}.tmp"
    tmp_gz = f"{out_gz}.{os.getpid()}.tmp"
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(tmp_db)
        try:
            src.backup(dst)   # one step: a consistent copy of the last committed state
        finally:
            dst.close()
            src.close()
        check = integrity(tmp_db)
        if check != "ok":
            raise BackupError(f"integrity check failed: {check[:200]}")
        with open(tmp_db, "rb") as f_in, open(tmp_gz, "wb") as raw:
            os.fchmod(raw.fileno(), 0o600)
            with gzip.GzipFile(filename=DB_NAME, mode="wb", fileobj=raw, mtime=0) as f_out:
                shutil.copyfileobj(f_in, f_out, 1 << 20)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp_gz, out_gz)
    except sqlite3.Error as exc:
        raise BackupError(f"snapshot failed: {exc}") from exc
    finally:
        _remove(tmp_db, tmp_gz)


def copy_file(src, dest):
    """Copy src to dest via a temporary name, so a half-copied file never looks complete."""
    tmp = f"{os.path.dirname(dest)}/.{os.path.basename(dest)}.{os.getpid()}.tmp"
    try:
        shutil.copyfile(src, tmp)
        shutil.copystat(src, tmp)
        os.replace(tmp, dest)
    finally:
        _remove(tmp)


def copy_new_files(src_dir, dest_dir):
    """Copy files from src_dir that dest_dir doesn't have yet. Photos are write-once (each upload gets a
    new random name), so a name that's already there is the same photo. Returns how many were copied."""
    os.makedirs(dest_dir, exist_ok=True)
    have = set(os.listdir(dest_dir))
    copied = 0
    for name in sorted(os.listdir(src_dir)) if os.path.isdir(src_dir) else []:
        src = os.path.join(src_dir, name)
        if name.startswith(".") or name in have or not os.path.isfile(src) or os.path.islink(src):
            continue
        copy_file(src, os.path.join(dest_dir, name))
        copied += 1
    return copied


def prune(root, filename, keep_days, today):
    """Delete root/<YYYY-MM-DD>/filename for days older than keep_days, and the day folder once empty.
    Nothing else is touched: another tool (app-backup) may keep its own files in the same folders."""
    cutoff = today - datetime.timedelta(days=keep_days)
    removed = 0
    for name in os.listdir(root) if os.path.isdir(root) else []:
        if not DAY_RE.fullmatch(name):
            continue
        try:
            day = datetime.date.fromisoformat(name)
        except ValueError:
            continue
        folder = os.path.join(root, name)
        if day >= cutoff or not os.path.isdir(folder) or os.path.islink(folder):
            continue
        path = os.path.join(folder, filename)
        if os.path.isfile(path):
            os.remove(path)
            removed += 1
        try:
            os.rmdir(folder)   # only succeeds when nothing else is in it
        except OSError:
            pass
    return removed


def run(data_dir, backup_dir, keep_days, target="", today=None, skip_if=()):
    """One backup run. Returns the exit status."""
    for path in skip_if:
        if os.path.exists(path):
            log(f"skipped: {path} backs up this host")
            return 0
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    stamp = today.isoformat()
    failed = False

    day_dir = os.path.join(backup_dir, stamp)
    out = os.path.join(day_dir, DB_NAME + ".gz")
    try:
        private_dir(backup_dir)
        private_dir(day_dir)
        snapshot(os.path.join(data_dir, DB_NAME), out)
        log(f"ok {out} ({os.path.getsize(out)} bytes)")
    except (BackupError, OSError) as exc:
        log(f"FAILED snapshot: {exc}")
        out, failed = None, True
    try:
        prune(backup_dir, DB_NAME + ".gz", keep_days, today)
    except OSError as exc:
        log(f"FAILED pruning {backup_dir}: {exc}")
        failed = True

    if target:
        if not (os.path.isdir(target) and os.access(target, os.W_OK | os.X_OK)):
            log(f"FAILED: BACKUP_TARGET {target} is not a writable folder (is it mounted?)")
            return 1
        try:
            if out:
                dest = os.path.join(target, "db", stamp)
                os.makedirs(dest, exist_ok=True)
                copy_file(out, os.path.join(dest, TARGET_DB_NAME))
            n = copy_new_files(os.path.join(data_dir, "photos"),
                               os.path.join(target, "files", "dinner-log", "photos"))
            prune(os.path.join(target, "db"), TARGET_DB_NAME, keep_days, today)
            log(f"copied to {target} ({n} new photo{'s' if n != 1 else ''})")
        except OSError as exc:
            log(f"FAILED copying to {target}: {exc}")
            failed = True
    return 1 if failed else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Back up Dinner Log's database (and photos to BACKUP_TARGET).")
    p.add_argument("--skip-if", action="append", default=[], metavar="PATH",
                   help="do nothing when PATH exists (repeatable)")
    args = p.parse_args(argv)
    data_dir = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    raw_keep = os.environ.get("BACKUP_KEEP_DAYS", "").strip().strip("'\"") or "14"
    if not (raw_keep.isdigit() and int(raw_keep) > 0):
        print(f"BACKUP_KEEP_DAYS must be a whole number of days above 0, not {raw_keep!r}", file=sys.stderr)
        return 2
    os.umask(0o077)
    return run(data_dir=data_dir,
               backup_dir=os.environ.get("BACKUP_DIR", "").strip() or os.path.join(data_dir, "backups"),
               keep_days=int(raw_keep),
               target=os.environ.get("BACKUP_TARGET", "").strip(),
               skip_if=args.skip_if)


if __name__ == "__main__":
    sys.exit(main())
