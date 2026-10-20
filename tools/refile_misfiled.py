#!/usr/bin/env python3
"""
Re-file downloaded episodes that sit in the wrong day folder or carry the wrong date.

Before 2026-10-19 the toolbox took days and dates from UTC, so evening shows
landed in the next day's folder with the next day's date; daily shows were all
filed under one day; and early (March 2026) downloads used an old
AutoArchive/2026/{slug}/{slug}_YYMMDD_HHMMSS layout. This moves each such file
to where the toolbox files it today:

    {nas.archive_path}/{weekday aired}/{Show}/{filename for the date aired}

and updates the episode's paths in the database. It never overwrites or
deletes a file: if the target already exists, the file is left alone and
reported. Files already in the right folder with the right date are not
touched, whatever their naming style (bracketed names stay as they are).

Run on the stream PC as the wdbx user, from the toolbox folder:

    python3 tools/refile_misfiled.py                  # dry run: print the plan, change nothing
    sudo systemctl stop wdbx-toolbox
    python3 tools/refile_misfiled.py --apply          # move files, update the DB, write a log
    sudo systemctl start wdbx-toolbox
    python3 tools/refile_misfiled.py --undo LOGFILE   # put everything back
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from archive_manager.downloader import _build_filename
from archive_manager.nas import nas_is_writable, sanitize_show_name
from shared.config import get
from shared.database import get_engine
from shared.models import Episode, IngestFile, Show
from shared.station_time import station_date

DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class Move:
    episode_id: int
    show_key: str
    old: Path
    new: Path
    problem: str = ""   # set when the move can't be made


def _is_misfiled(path: Path, aired_day: str, aired_date: str) -> bool:
    """Wrong weekday folder, or a filename that starts with the wrong date."""
    m = DATE_PREFIX.match(path.name)
    return path.parent.parent.name != aired_day or (m is not None and m.group() != aired_date)


def plan(session: Session, base: Path) -> tuple[list[Move], Counter]:
    """Work out every move. Returns (moves, counts of episodes skipped and why)."""
    shows = {s.show_key: s for s in session.exec(select(Show)).all()}
    episodes = session.exec(select(Episode).where(Episode.status == "downloaded")).all()
    moves: list[Move] = []
    skipped: Counter = Counter()

    for ep in episodes:
        if not ep.nas_path or not Path(ep.nas_path).is_relative_to(base):
            skipped["not on the NAS"] += 1
            continue
        show = shows.get(ep.show_key)
        if not show or not show.display_name:
            skipped["no show record"] += 1
            continue
        old = Path(ep.nas_path)
        aired = station_date(ep.air_datetime)
        if not _is_misfiled(old, aired.strftime("%A"), aired.isoformat()):
            continue
        new = base / aired.strftime("%A") / sanitize_show_name(show.display_name) / _build_filename(ep, show)
        moves.append(Move(ep.id, ep.show_key, old, new))

    sources = {m.old for m in moves}
    for m in moves:
        if not m.old.exists():
            m.problem = "file missing"
        elif m.new.exists() and m.new not in sources:
            # A target held by another file that is itself moving gets freed first
            m.problem = "target already exists"
    return moves, skipped


def _set_paths(session: Session, episode_id: int, old: Path, new: Path) -> None:
    ep = session.get(Episode, episode_id)
    if ep.nas_path == str(old):
        ep.nas_path = str(new)
    if ep.local_path == str(old):
        ep.local_path = str(new)
    session.add(ep)
    for f in session.exec(select(IngestFile).where(IngestFile.file_path == str(old))).all():
        f.file_path = str(new)
        session.add(f)
    session.commit()


def _move(session: Session, episode_id: int, src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)
    try:
        _set_paths(session, episode_id, src, dst)
    except Exception:
        os.rename(dst, src)  # keep the file where the DB says it is
        raise


def _remove_empty_dirs(dirs: set[Path], base: Path) -> None:
    """Remove folders emptied by the moves, walking up but never above base."""
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        while d != base and d.is_relative_to(base):
            try:
                d.rmdir()   # only succeeds when empty
            except OSError:
                break
            d = d.parent


def apply(session: Session, moves: list[Move], base: Path, log_path: Path) -> tuple[int, list[Move]]:
    """Make the moves, in passes so a target held by another moving file is freed first."""
    pending = [m for m in moves if not m.problem]
    done = 0
    emptied: set[Path] = set()
    with open(log_path, "w", newline="") as f:
        log = csv.writer(f, delimiter="\t")
        log.writerow(["episode_id", "old", "new"])
        while pending:
            progressed = False
            for m in list(pending):
                if m.new.exists():
                    continue
                _move(session, m.episode_id, m.old, m.new)
                log.writerow([m.episode_id, m.old, m.new])
                f.flush()
                emptied.add(m.old.parent)
                pending.remove(m)
                done += 1
                progressed = True
            if not progressed:
                break
    for m in pending:
        m.problem = "target still occupied"
    _remove_empty_dirs(emptied, base)
    return done, pending


def undo(session: Session, log_path: Path) -> tuple[int, int]:
    """Reverse a log written by apply(). Returns (moved back, skipped)."""
    with open(log_path, newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    back = skipped = 0
    for row in reversed(rows):
        old, new = Path(row["old"]), Path(row["new"])
        if new.exists() and not old.exists():
            _move(session, int(row["episode_id"]), new, old)
            back += 1
        else:
            skipped += 1
    return back, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="move the files and update the database")
    group.add_argument("--undo", metavar="LOGFILE", help="reverse a previous --apply")
    args = parser.parse_args()

    base = Path(get("nas.archive_path", "/mnt/wdbx-share/Shows/AutoArchive"))
    if not nas_is_writable():
        sys.exit(f"The NAS is not mounted and writable at {base}; nothing done.")

    with Session(get_engine()) as session:
        if args.undo:
            back, skipped = undo(session, Path(args.undo))
            print(f"Moved {back} file(s) back; skipped {skipped} (already back, or path changed since).")
            return

        moves, skipped = plan(session, base)
        problems = [m for m in moves if m.problem]
        ok = [m for m in moves if not m.problem]
        print(f"{len(moves)} misfiled episode(s): {len(ok)} to move, {len(problems)} that can't be moved.")
        for reason, n in skipped.items():
            print(f"  (ignored {n} downloaded episode(s): {reason})")
        for key, n in Counter(m.show_key for m in ok).most_common():
            print(f"  {n:4d}  {key}")
        for m in problems:
            print(f"  CAN'T MOVE ({m.problem}): {m.old}  ->  {m.new}")

        if not args.apply:
            plan_path = Path.home() / "refile_plan.tsv"
            with open(plan_path, "w", newline="") as f:
                w = csv.writer(f, delimiter="\t")
                w.writerow(["episode_id", "show_key", "old", "new", "problem"])
                for m in moves:
                    w.writerow([m.episode_id, m.show_key, m.old, m.new, m.problem])
            print(f"\nDry run: nothing changed. Every planned move is listed in {plan_path}")
            print("To make the moves: stop the service, then rerun with --apply.")
            return

        log_path = Path.home() / f"refile_log_{datetime.now():%Y%m%d_%H%M%S}.tsv"
        done, stuck = apply(session, moves, base, log_path)
        print(f"\nMoved {done} file(s). Log: {log_path}")
        for m in stuck:
            print(f"  NOT MOVED ({m.problem}): {m.old}")
        print(f"To reverse: python3 tools/refile_misfiled.py --undo {log_path}")


if __name__ == "__main__":
    main()
