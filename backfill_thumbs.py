"""Write the 480 px thumbnail for photos saved before Dinner Log made thumbnails.

    python3 -m backfill_thumbs --dry-run     # list what would be written, write nothing
    python3 -m backfill_thumbs               # write the missing thumbnails

Reads DATA_DIR like the app. For every photo row it prints one line and ends with a total. A photo that
already has its <name>-t.jpg is left alone, so running it again changes nothing. Thumbnails are made from
the stored 1600 px image with the same decoder checks as an upload, and written atomically. The database is
opened read-only.

Exit status: 0 done, 1 if any photo couldn't be done (its file is missing or unreadable, or its stored name
isn't a plain file name); the other photos are still processed.
"""
import argparse
import os
import sqlite3
import sys

import app


def status(filename, dry_run):
    """(outcome, detail) for one stored photo: has, would, wrote, missing, unsafe or error."""
    if not filename or os.path.basename(filename) != filename or filename in (".", ".."):
        return "unsafe", "stored name isn't a plain file name"
    source = os.path.join(app.PHOTO_DIR, filename)
    if os.path.isfile(os.path.join(app.PHOTO_DIR, app.thumb_name(filename))):
        return "has", "already has a thumbnail"
    if not os.path.isfile(source):
        return "missing", "source file not found"
    if dry_run:
        return "would", "would write a thumbnail"
    try:
        with open(source, "rb") as fh:
            img = app.load_photo(fh)
        app.write_thumb(img, filename)
        with open(os.path.join(app.PHOTO_DIR, app.thumb_name(filename)), "rb") as fh:
            thumb = app.Image.open(fh)
            size = thumb.size
    except Exception as exc:  # noqa: BLE001 - report it and carry on with the rest
        return "error", f"{type(exc).__name__}: {exc}"
    return "wrote", f"wrote a thumbnail ({size[0]}x{size[1]})"


def main(argv=None, out=sys.stdout):
    p = argparse.ArgumentParser(prog="python3 -m backfill_thumbs", description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="print what would be written and write nothing")
    args = p.parse_args(argv)
    con = sqlite3.connect(f"file:{app.DB_PATH}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute("SELECT id, filename FROM photos ORDER BY id").fetchall()
    finally:
        con.close()
    counts = dict.fromkeys(("has", "would", "wrote", "missing", "unsafe", "error"), 0)
    for photo_id, filename in rows:
        outcome, detail = status(filename, args.dry_run)
        counts[outcome] += 1
        print(f"photo {photo_id:>5}  {filename}: {detail}  [{outcome}]", file=out)
    errors = counts["missing"] + counts["unsafe"] + counts["error"]
    if args.dry_run:
        done = f"would write {counts['would']} thumbnails"
    else:
        done = f"wrote {counts['wrote']} thumbnails"
    print(f"{len(rows)} photos checked: {done}, {counts['has']} already had one, {errors} errors"
          + (" (dry run: nothing written)" if args.dry_run else ""), file=out)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
