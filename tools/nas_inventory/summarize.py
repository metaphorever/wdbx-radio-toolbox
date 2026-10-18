#!/usr/bin/env python3
"""Summarize ~/inventory/nas.db: where the space goes and how much looks duplicated."""
import os
import sqlite3

DB = os.path.expanduser("~/inventory/nas.db")
AUDIO = ("mp3", "wav", "flac", "m4a", "aif", "aiff", "ogg", "wma", "aac")
MIN_SIZE = 1024 * 1024  # ignore small files in duplicate estimates


def gb(n):
    return f"{(n or 0) / 1e9:8.1f} GB"


con = sqlite3.connect(DB)
con.execute("CREATE INDEX IF NOT EXISTS idx_name_size ON files (name, size)")
q = con.execute

print("== By share")
for share, n, total in q("SELECT share, COUNT(*), SUM(size) FROM files GROUP BY share ORDER BY 3 DESC"):
    print(f"{gb(total)}  {n:8d} files  {share}")

print("\n== Top file types by size")
for ext, n, total in q("SELECT ext, COUNT(*), SUM(size) FROM files GROUP BY ext ORDER BY 3 DESC LIMIT 20"):
    print(f"{gb(total)}  {n:8d} files  .{ext or '(none)'}")

marks = ",".join("?" * len(AUDIO))
audio = q(f"SELECT SUM(size) FROM files WHERE ext IN ({marks})", AUDIO).fetchone()[0] or 0
total = q("SELECT SUM(size) FROM files").fetchone()[0] or 0
print(f"\n== Audio {gb(audio)}   Other {gb(total - audio)}   Total {gb(total)}")

# Same name AND same size: very likely the same file copied around.
extra, n = q("""SELECT SUM(size * (n - 1)), SUM(n - 1) FROM
    (SELECT size, COUNT(*) n FROM files WHERE size >= ? GROUP BY name, size HAVING n > 1)""",
    (MIN_SIZE,)).fetchone()
print(f"\n== Redundant copies (same name + size): {gb(extra)} in {n or 0} extra files")

# Same size only: an upper bound. Fixed-length stream recordings can share a size
# without being the same show, so this overstates duplicates.
extra = q("""SELECT SUM(size * (n - 1)) FROM
    (SELECT size, COUNT(*) n FROM files WHERE size >= ? GROUP BY size HAVING n > 1)""",
    (MIN_SIZE,)).fetchone()[0]
print(f"== Upper bound (same size only):         {gb(extra)}")

print("\n== Files in one share that also exist in another (same name + size)")
shares = [r[0] for r in q("SELECT DISTINCT share FROM files")]
for a in shares:
    for b in shares:
        if a != b:
            n, s = q("""SELECT COUNT(*), SUM(size) FROM files x
                WHERE x.share = ? AND x.size >= ? AND EXISTS (SELECT 1 FROM files y
                    WHERE y.share = ? AND y.name = x.name AND y.size = x.size)""",
                (a, MIN_SIZE, b)).fetchone()
            if n:
                print(f"{gb(s)}  {n:8d} files  in {a}, also in {b}")

errors = q("SELECT COUNT(*) FROM errors").fetchone()[0]
print(f"\n{errors} paths could not be read (see the errors table)")
