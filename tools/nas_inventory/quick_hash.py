#!/usr/bin/env python3
"""Quick-hash in-scope files that share their exact size with another in-scope file.

Reads only the first and last 64KB of each candidate (MD5 of size + head + tail),
so it is cheap over the network. A match is strong evidence of a duplicate,
not proof; confirm with a full hash before relying on it. Resumable.
"""
import hashlib
import os
import sqlite3
import time

ROOT = "/mnt/nas"
DB = os.path.expanduser("~/inventory/nas.db")
CHUNK = 64 * 1024
MIN_SIZE = 1024 * 1024


def qhash(path, size):
    h = hashlib.md5(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        f.seek(max(size - CHUNK, 0))
        h.update(f.read(CHUNK))
    return h.hexdigest()


con = sqlite3.connect(DB)
if "qhash" not in [r[1] for r in con.execute("PRAGMA table_info(files)")]:
    con.execute("ALTER TABLE files ADD COLUMN qhash TEXT")
# Rebuild the view so it includes the new column.
con.execute("DROP VIEW IF EXISTS scope")
con.execute("""CREATE VIEW scope AS
    SELECT f.* FROM files f WHERE NOT EXISTS (
        SELECT 1 FROM exclude e WHERE e.share = f.share
        AND substr(f.relpath, 1, length(e.prefix)) = e.prefix)""")
con.commit()

todo = con.execute("""SELECT share, relpath, size FROM scope
    WHERE qhash IS NULL AND size >= ? AND size IN
    (SELECT size FROM scope WHERE size >= ? GROUP BY size HAVING COUNT(*) > 1)""",
    (MIN_SIZE, MIN_SIZE)).fetchall()
print(f"{len(todo)} candidate files", flush=True)
start = time.time()
for i, (share, rel, size) in enumerate(todo, 1):
    try:
        h = qhash(os.path.join(ROOT, share, rel), size)
    except OSError as err:
        con.execute("INSERT INTO errors VALUES (?, ?)", (f"{share}/{rel}", str(err)))
        continue
    con.execute("UPDATE files SET qhash = ? WHERE share = ? AND relpath = ?", (h, share, rel))
    if i % 500 == 0:
        con.commit()
        print(f"{i}/{len(todo)}  {time.time() - start:.0f}s", flush=True)
con.commit()
print(f"done in {time.time() - start:.0f}s", flush=True)
