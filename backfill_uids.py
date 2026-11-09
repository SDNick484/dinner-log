"""Fill in Cookbook Collection uids on meals logged before Dinner Log stored them.

    python3 -m backfill_uids --dry-run     # print what would change, write nothing
    python3 -m backfill_uids               # write it

Reads DATA_DIR and COOKBOOK_API_URL (or COOKBOOK_URL) like the app, and calls only the catalog's public
GET /api/books/<id>, once per distinct book.

Only meals that have a Cookbook Collection int id and no uid are touched, and a uid that is already set is
never replaced, so running it again changes nothing. A uid is written only when the catalog still agrees
with what the meal recorded: Cookbook Collection's ids are SQLite rowids and can be reused after a delete,
and a uid stamped from a reused id would be trusted over everything else from then on.

- book uid:   the catalog book's title matches the meal's cookbook title (case, accents, "The" ignored)
- recipe uid: the book matched, the recipe is in that book, and its page or its name matches the meal's

Anything else is reported and left NULL; Cookbook Collection keeps matching those meals by int and title.
Exit status: 0 done, 1 not configured or the catalog didn't answer (nothing is written then).
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

import app

TIMEOUT = 10


def fetch_book(book_id):
    """The catalog's public book JSON, or None when it 404s (deleted, or a website: those are editor-only).
    Any other failure raises: a partial run would report books as gone that are only unreachable."""
    url = f"{app.COOKBOOK_API_URL}/api/books/{int(book_id)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with app._cookbook_opener.open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _first_number(s):
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else None


def recipe_agrees(recipe, meal):
    """The meal's page is the recipe's page, or one name's words are all in the other's
    ("Roast chicken" and "Zuni Roast Chicken with Bread Salad")."""
    page, mine = _first_number(recipe.get("page")), _first_number(meal["source_detail"])
    if page is not None and page == mine:
        return True
    name, dinner = app._words(recipe.get("name")), app._words(meal["name"])
    full = name | app._words(recipe.get("heading"))  # the 🍽 link names some dinners "heading, name"
    return bool(name and dinner) and (name <= dinner or dinner <= full)


def plan(con, fetch=fetch_book):
    """[(meal row, {"book_uid", "recipe_uid"} to write, note)] for every meal that needs a look."""
    rows = con.execute(
        "SELECT id, day, name, source_title, source_detail, cookbook_book_id, cookbook_recipe_id, "
        "cookbook_book_uid, cookbook_recipe_uid FROM meals "
        "WHERE source_type IN ('cookbook', 'online') AND ("
        "  (cookbook_book_id IS NOT NULL AND cookbook_book_uid IS NULL) OR "
        "  (cookbook_recipe_id IS NOT NULL AND cookbook_recipe_uid IS NULL)) ORDER BY id").fetchall()
    books = {}
    for bid in sorted({r["cookbook_book_id"] for r in rows if r["cookbook_book_id"] is not None}):
        books[bid] = fetch(bid)
    out = []
    for m in rows:
        if m["cookbook_book_id"] is None:
            out.append((m, {}, "skip: recipe id without a book id"))
            continue
        book = books[m["cookbook_book_id"]]
        if book is None:
            out.append((m, {}, f"skip: book {m['cookbook_book_id']} isn't in the public catalog (deleted, or a website)"))
            continue
        book_uid = app.parse_cookbook_uid(book.get("uid"))
        if not book_uid:
            out.append((m, {}, "skip: the catalog sends no uids (upgrade Cookbook Collection first)"))
            continue
        if app.title_key(book.get("title")) != app.title_key(m["source_title"]):
            out.append((m, {}, f"conflict: book {m['cookbook_book_id']} is now {book.get('title')!r}, "
                               f"the meal says {m['source_title']!r}"))
            continue
        write, notes = {}, []
        if m["cookbook_book_uid"] is None:
            write["book_uid"] = book_uid
        if m["cookbook_recipe_id"] is not None and m["cookbook_recipe_uid"] is None:
            recipe = next((r for r in book.get("recipes", []) if r.get("id") == m["cookbook_recipe_id"]), None)
            recipe_uid = app.parse_cookbook_uid(recipe.get("uid")) if recipe else None
            if recipe is None:
                notes.append(f"recipe {m['cookbook_recipe_id']} is no longer in this book")
            elif not recipe_uid:
                notes.append("the recipe has no uid")
            elif not recipe_agrees(recipe, m):
                notes.append(f"recipe {m['cookbook_recipe_id']} is now {recipe.get('name')!r} p. {recipe.get('page') or '-'}")
            else:
                write["recipe_uid"] = recipe_uid
        note = ("conflict (recipe left NULL): " + "; ".join(notes)) if notes else "ok"
        out.append((m, write, note))
    return out


def apply(con, decisions):
    """Write the planned uids in one transaction, never over a uid that is already set."""
    n = 0
    with con:
        for m, write, _ in decisions:
            if "book_uid" in write:
                n += con.execute("UPDATE meals SET cookbook_book_uid=? WHERE id=? AND cookbook_book_uid IS NULL",
                                 (write["book_uid"], m["id"])).rowcount
            if "recipe_uid" in write:
                n += con.execute("UPDATE meals SET cookbook_recipe_uid=? WHERE id=? AND cookbook_recipe_uid IS NULL",
                                 (write["recipe_uid"], m["id"])).rowcount
    return n


def report(decisions, dry_run, out=sys.stdout):
    verb = "would set" if dry_run else "set"
    for m, write, note in decisions:
        sets = ", ".join(f"{k}={v}" for k, v in write.items()) or "nothing"
        print(f"meal {m['id']:>5}  {m['day']}  {m['name'][:40]!r}: {verb} {sets}  [{note}]", file=out)
    books = sum("book_uid" in w for _, w, _ in decisions)
    recipes = sum("recipe_uid" in w for _, w, _ in decisions)
    skipped = sum(not w for _, w, _ in decisions)
    conflicts = sum(n.startswith("conflict") for _, _, n in decisions)
    print(f"{len(decisions)} meals checked: {verb} {books} book uids and {recipes} recipe uids; "
          f"{conflicts} conflicts, {skipped} meals left unchanged"
          + (" (dry run: nothing written)" if dry_run else ""), file=out)


def main(argv=None, fetch=fetch_book, out=sys.stdout):
    p = argparse.ArgumentParser(prog="python3 -m backfill_uids", description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="print what would change and write nothing")
    args = p.parse_args(argv)
    if fetch is fetch_book and not app.COOKBOOK_API_URL:
        print("COOKBOOK_API_URL (or COOKBOOK_URL) is not set.", file=sys.stderr)
        return 1
    con = app.sqlite3.connect(app.DB_PATH, timeout=30)
    con.row_factory = app.sqlite3.Row
    try:
        try:
            decisions = plan(con, fetch)
        except Exception as e:  # catalog down or a bad reply: write nothing
            print(f"Cookbook Collection didn't answer ({e}); nothing was written.", file=sys.stderr)
            return 1
        if not args.dry_run:
            apply(con, decisions)
        report(decisions, args.dry_run, out)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
