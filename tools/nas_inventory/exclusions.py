#!/usr/bin/env python3
"""Record which NAS folders are not station archive, and (re)build the `scope` view.

An empty prefix excludes the whole share. Safe to re-run; add new rows to EXCLUDE
as more non-archive folders are identified. See docs/nas_consolidation.md.
"""
import os
import sqlite3

DB = os.path.expanduser("~/inventory/nas.db")

EXCLUDE = [
    ("Add to Share", "IAN IMPORT 2023 - DO NOT MESS WITH PLEASE/"),
    ("Add to Share", "Darby - DO NOT DELETE/"),
    ("Add to Share", "2026 CD Import/"),
    ("Prerecorded Shows", ""),
    ("Music", ""),
]

con = sqlite3.connect(DB)
con.execute("CREATE TABLE IF NOT EXISTS exclude (share TEXT, prefix TEXT, PRIMARY KEY (share, prefix))")
con.executemany("INSERT OR IGNORE INTO exclude VALUES (?, ?)", EXCLUDE)
# Rebuilt every run so the view always includes any columns added to `files`.
con.execute("DROP VIEW IF EXISTS scope")
con.execute("""CREATE VIEW scope AS
    SELECT f.* FROM files f WHERE NOT EXISTS (
        SELECT 1 FROM exclude e WHERE e.share = f.share
        AND substr(f.relpath, 1, length(e.prefix)) = e.prefix)""")
con.commit()
