# NAS Consolidation — Inventory & Plan
*Last updated: 2026-10-18*

Expands runbook §7.2. Goal: build **one canonical copy of every show** — canonical names, schema and folder hierarchy — on the T610 ([`t610_server.md`](t610_server.md)), instead of mirroring the NAS's redundant copies, old PC backups and random MP3s onto the new server.

---

## Where We Left Off (2026-10-18)

- Full read-only inventory of the NAS is done: `~/inventory/nas.db` on the T610.
- Exclusions recorded (see §3). **In scope: 2,730 GB in 25,471 files.**
- `quick_hash.py` was running on the T610 in the background over Tailscale: 19,643 candidate files, ~158 s per 500 → roughly 1 h 40 min after the remount. Check it with `tail ~/inventory/quick_hash.log`; it is resumable, so if it died just start it again.
- **Next:** when it says `done`, run `python3 ~/inventory/scope_summary.py`. The "Copies by content … leaves" line is the best estimate of the real archive size, and decides whether it fits on `testpool` (~1.85 TB comfortable, ~2.3 TB raw) or needs bigger drives.

---

## 1. Principles

1. **Copy forward; never curate in place.** The canonical archive is built as a new copy. Nothing on the NAS is moved, renamed or deleted during this project.
2. **The 5 TB backup stays a cold raw dump of everything** (including the excluded folders) — insurance against "we threw away something we needed."
3. **Read-only access.** All NAS shares are mounted `ro` on the T610.
4. **Duplicates are proven, not guessed.** Size groups → quick hash (first/last 64 KB) to find candidates cheaply over the WAN → full-file hash on the station LAN before any copy is treated as redundant.
5. **Respect other people's folders** ("DO NOT MESS WITH", "DO NOT DELETE") — they're excluded, not touched.

---

## 2. Backup Status

- 5 TB WD My Passport attached to the NAS, running **nightly scheduled backups** of the NAS (share `USB_WD_My_Passport_2626_c_1` — excluded from the inventory since it's a copy).
- NAS CPU fan (warning 2026-08-07) replaced; NAS stable.
- The backup drive also covers the studio PC's overnight-pull role if the NAS dies.
- NAS total is ~3.15 TB (decimal) of user data, so the 5 TB drive has room, but watch it as the archive grows.

---

## 3. Shares and Scope

| Share | Size (GB) | Files | Scope |
|-------|-----------|-------|-------|
| Add to Share | 1,838.7 | 32,251 | In, minus exclusions below. Toolbox's live share (`Shows/AutoArchive`) |
| Show Archives | 1,110.9 | 12,442 | In, minus `Misc Music/` |
| Music | 121.4 | 13,700 | **Out** — random MP3s, no show archives (separate future project) |
| Prerecorded Shows | 74.6 | 2,100 | **Out** — syndicated (Radiolab, Twilight Zone, …) |
| Underwriting | 0.1 | 120 | In |
| Backups, Documents, QuikTransfer | — | — | NAS system shares; `rsync` user can't mount; irrelevant |
| USB_WD_My_Passport_2626_c_1 | — | — | The backup drive; skip |

Excluded folders (stored in the `exclude` table, applied through the `scope` view):

| Share | Prefix | Why |
|-------|--------|-----|
| Add to Share | `IAN IMPORT 2023 - DO NOT MESS WITH PLEASE/` | Someone else's project |
| Add to Share | `Darby - DO NOT DELETE/` | Someone else's project |
| Add to Share | `2026 CD Import/` | Music import, not archive |
| Prerecorded Shows | *(whole share)* | Syndicated content |
| Music | *(whole share)* | Random MP3s |
| Show Archives | `Misc Music/` | Random MP3s (incl. `OVERNIGHT III`) |

**Keep in scope and treat carefully:** `Show Archives/SORT THESE FILES/` (56 GB). Mostly raw archive downloads, likely including pre-auto-archive manual downloads from the old station manager PC backup — possibly the **only** copies of early shows. After hashing, list its files with no copy anywhere else; that's the "don't lose these" set.

---

## 4. Inventory Results

Whole NAS (excluding the backup drive): **3,145.7 GB, 60,613 files, 0 read errors.**

- Audio 3,069 GB (`.mp3` 2,962 GB / 53,615 files; also flac 48, wav 34, m4a 24 GB). Non-audio only ~77 GB (video ~51 GB, zip 25 GB).
- Same name **and** size (strong duplicates): 621.6 GB in 9,856 extra files. About 450 GB of that is shows present in both Show Archives and Add to Share.
- Same size only: 2,226.6 GB — a ceiling, not an estimate. ~19.6k of 25.5k in-scope files share an exact size with another file; fixed-length stream recordings collide.
- **In scope:** 2,730.0 GB → after same-name copies **2,171.3 GB**.
- The unknown is **renamed copies**: the toolbox renames raw `wdbx_YYMMDD_HHMMSS.mp3` downloads to the canonical template, so the same recording can live under two names. `quick_hash.py` settles this.

Biggest folders (GB): `Add to Share/Shows/AutoArchive` 791 · `Add to Share/Shows/{1 - Sunday … 7 - Saturday}` ~73–159 each · `Show Archives/Auto Archive/{Monday,Tuesday}` ~70 each · `Show Archives/SORT THESE FILES` 56 · `Show Archives/{N - Day}/{Show}` many 10–43.

So the NAS has two parallel day-of-week hierarchies (`Add to Share/Shows/N - Day/` and `Show Archives/N - Day/<Show>/`) plus two auto-archive trees (toolbox `Add to Share/Shows/AutoArchive/` and an older `Show Archives/Auto Archive/<Day>/`).

---

## 5. Tooling

Scripts live in [`tools/nas_inventory/`](../tools/nas_inventory/) (stdlib only) and were run from `~/inventory/` on the T610. The database is `~/inventory/nas.db`.

| Script | Does |
|--------|------|
| `nas_inventory.py` | Walks every share mounted under `/mnt/nas`, records share/path/name/ext/size/mtime. Never opens file contents |
| `summarize.py` | Whole-NAS totals by share and type, duplicate estimates, cross-share overlap |
| `exclusions.py` | Creates the `exclude` table with the list above and the `scope` view |
| `quick_hash.py` | MD5 of size + first/last 64 KB for in-scope files that share a size with another; resumable; adds `files.qhash` |
| `scope_summary.py` | In-scope totals, same-name copies, content (quick-hash) copies |

Mounts on the T610 (manual, not in fstab, gone after reboot):
```bash
# creds: /etc/samba/wdbx-nas.creds (user rsync), same as the stream PC
for s in "Show Archives" "Add to Share" "Underwriting"; do
  sudo mkdir -p "/mnt/nas/$s"
  sudo mount -t cifs "//192.168.2.185/$s" "/mnt/nas/$s" \
    -o ro,cache=none,credentials=/etc/samba/wdbx-nas.creds,uid=wdbx,gid=wdbx,iocharset=utf8,vers=3.1.1
done
```
`cache=none` matters for the quick hash: with the default page cache, CIFS read-ahead pulled ~18× more data than requested over the WAN.

---

## 6. Next Steps

1. Final quick-hash numbers → decide whether the curated archive fits on `testpool`. If not: the spare 3 TB drive has no free bay, so the route is bigger drives (e.g. the NAS's 2 TB drives once retired) in a new pool + `zfs send`.
2. List the unique files in `SORT THESE FILES`.
3. Define the canonical schema. Starting points already in the repo: filename template `{date} - {show} - WDBX` (runbook §5.3), hierarchy `{year}/{show-slug}/`, and `ingest/classifier.parse_filename`, which already recognises seven naming schemes (raw `wdbx_YYMMDD_HHMMSS`, both toolbox formats, …).
4. Move the T610 to the station. Full-file hashes and the canonical copy run over the station LAN — at ~3.7 MB/s, 2 TB over Tailscale would take about a week.
5. Second pass with audio fingerprints for same-show-different-file cases (re-encodes, trims, fragments).

---

## 7. Toolbox Issues Found While Reviewing `ingest/`

Fix before using the ingest pipeline to build the canonical copy:

- **`ingest/crawler.py` `copy_to` collisions:** `_staging_dest`'s docstring promises a counter suffix on name collisions, but the code doesn't add one. A different file at the same relative path is treated as already copied — a unique show could be silently left out.
- **`_fast_hash`** (MD5 of first + last 64 KB, size not included) is a proxy. Fine for finding candidates; confirm with a full hash before treating files as identical.
- **`ingest/fingerprinter._pick_canonical`:** the documented "earliest crawled" tie-break isn't implemented, so ties pick arbitrarily.
- **`compare_fingerprints`** compares without offset alignment, so re-airs/fragments that start at different points will score low.
- **`archive_manager/mailer.send_alert`** skips sending with only a DEBUG log when `smtp.host` is empty (log level is INFO), so an unconfigured mailer fails silently. Log a warning at startup instead.
- `crawler.py` only looks at `*.mp3` and doesn't record mtime.
