#!/usr/bin/env python3
"""Read-only inventory of the NAS shares mounted under /mnt/nas into SQLite.

Records path, size and mtime for every file. Never opens file contents.
Re-running replaces earlier rows for the same paths.
"""
import os
import sqlite3
import sys
import time

ROOT = "/mnt/nas"
DB = os.path.expanduser("~/inventory/nas.db")


def walk(con, share):
    base = os.path.join(ROOT, share)
    stack, count, start = [base], 0, time.time()
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError as err:
            con.execute("INSERT INTO errors VALUES (?, ?)", (d, str(err)))
            continue
        rows = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    ext = os.path.splitext(e.name)[1].lower().lstrip(".")
                    rows.append((share, os.path.relpath(e.path, base), e.name, ext,
                                 st.st_size, st.st_mtime))
            except OSError as err:
                con.execute("INSERT INTO errors VALUES (?, ?)", (e.path, str(err)))
        con.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)", rows)
        count += len(rows)
    con.commit()
    print(f"{share}: {count} files in {time.time() - start:.0f}s", flush=True)


def main():
    shares = sys.argv[1:] or sorted(
        s for s in os.listdir(ROOT) if os.path.ismount(os.path.join(ROOT, s)))
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS files (
        share TEXT, relpath TEXT, name TEXT, ext TEXT, size INTEGER, mtime REAL,
        PRIMARY KEY (share, relpath))""")
    con.execute("CREATE TABLE IF NOT EXISTS errors (path TEXT, error TEXT)")
    for share in shares:
        walk(con, share)
    con.close()


if __name__ == "__main__":
    main()
