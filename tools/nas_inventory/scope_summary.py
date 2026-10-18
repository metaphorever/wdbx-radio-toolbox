#!/usr/bin/env python3
"""Totals for the in-scope files: everything not matched by the exclude table."""
import os
import sqlite3

MIN_SIZE = 1024 * 1024
con = sqlite3.connect(os.path.expanduser("~/inventory/nas.db"))
q = con.execute


def gb(n):
    return f"{(n or 0) / 1e9:8.1f} GB"


total, n = q("SELECT SUM(size), COUNT(*) FROM scope").fetchone()
print(f"In scope: {gb(total)} in {n} files")
for share, s in q("SELECT share, SUM(size) FROM scope GROUP BY share ORDER BY 2 DESC"):
    print(f"  {gb(s)}  {share}")

named = q("""SELECT SUM(size * (c - 1)) FROM (SELECT size, COUNT(*) c FROM scope
    WHERE size >= ? GROUP BY name, size HAVING c > 1)""", (MIN_SIZE,)).fetchone()[0]
print(f"\nCopies by name + size: {gb(named)}   leaves {gb(total - (named or 0))}")

hashed = q("""SELECT SUM(size * (c - 1)) FROM (SELECT size, COUNT(*) c FROM scope
    WHERE qhash IS NOT NULL GROUP BY qhash HAVING c > 1)""").fetchone()[0]
print(f"Copies by content:     {gb(hashed)}   leaves {gb(total - (hashed or 0))}")
