"""
Live integration tests for the Confessor schedule and archive APIs.

These tests make real network calls. They are skipped in normal test runs.
Run them with:
    pytest -m live -s

The -s flag shows print output, which is the main artifact here — each test
documents what the API actually returns so we know what we can rely on before
replacing the HTML scrapers in schedule_scraper.py.

Open questions these tests are designed to answer:
  Q1. Do sh_altid slugs from getshows match slugs the archive API accepts?
  Q2. Does the altids endpoint return all archived shows, or only scheduled ones?
  Q3. What shape does getgone return, and is it usable for auto-updating is_gone?
  Q4. What does the archive API return for a show with no entries (gone/unknown slug)?
  Q5. Are expires and lsecs reliably present, or do we need fallback handling?
"""
import pytest

from archive_manager.confessor_client import (
    ArchiveEntry,
    ScheduledShow,
    get_all_shows,
    get_archive_entries,
    get_gone_shows,
    get_show_altids,
    get_show_by_key,
)

# ---------------------------------------------------------------------------
# Raw response inspection — run these first to understand actual API shapes
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_raw_getshows_response():
    """
    Inspect the raw getshows response before any parsing.
    First run revealed it's a list, not a day-keyed dict as the PHP docs suggest.
    This test documents the actual structure.
    """
    import requests
    resp = requests.get("https://confessor.wdbx.org/_do_api.php?req=getshows&json=1", timeout=30)
    resp.raise_for_status()
    data = resp.json()

    print(f"\n  Response type: {type(data).__name__}")
    print(f"  Length: {len(data)}")
    if isinstance(data, list) and data:
        first = data[0]
        print(f"  First element type: {type(first).__name__}")
        if isinstance(first, dict):
            print(f"  First element keys: {list(first.keys())}")
            print(f"  First element sample:")
            for k, v in first.items():
                print(f"    {k}: {v!r}")
        elif isinstance(first, list):
            print(f"  First sub-list length: {len(first)}")
            if first and isinstance(first[0], dict):
                print(f"  First sub-list[0] keys: {list(first[0].keys())}")
    elif isinstance(data, dict):
        print(f"  Keys: {list(data.keys())[:10]}")


@pytest.mark.live
def test_raw_altids_response():
    """
    First run showed altids returns stringified PHP arrays, not plain slugs.
    Inspect further to understand if this is parseable or if getshows is better.
    """
    import requests
    resp = requests.get("https://confessor.wdbx.org/_do_api.php?req=altids&json=1", timeout=30)
    resp.raise_for_status()

    print(f"\n  Status: {resp.status_code}")
    print(f"  Content-Type: {resp.headers.get('content-type')}")
    print(f"  Raw (first 500 chars): {resp.text[:500]}")

    try:
        data = resp.json()
        print(f"\n  Parsed type: {type(data).__name__}")
        print(f"  Length: {len(data)}")
        if isinstance(data, list) and data:
            print(f"  First 3 elements:")
            for item in data[:3]:
                print(f"    {type(item).__name__}: {item!r}")
    except Exception as e:
        print(f"  JSON parse error: {e}")


# A show key confirmed working in the archive as of deployment (2026-04-01).
# Used as a baseline sanity check — update if this show goes off-air.
KNOWN_GOOD_KEY = "islandreport"


# ---------------------------------------------------------------------------
# Schedule API — getshows
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_schedule_getshows_returns_list():
    """getshows returns a non-empty list of ScheduledShow with required fields."""
    shows = get_all_shows()

    print(f"\n  Total scheduled shows: {len(shows)}")
    for day_num in range(7):
        day_shows = [s for s in shows if s.day_num == day_num]
        if day_shows:
            print(f"  Day {day_num} ({day_shows[0].day}): {len(day_shows)} shows")

    assert len(shows) > 0, "Expected at least one scheduled show"

    for s in shows:
        assert s.show_key, f"show_key is empty: {s}"
        assert s.display_name, f"display_name is empty for key={s.show_key}"
        assert s.day, f"day is empty for key={s.show_key}"
        assert 0 <= s.day_num <= 6, f"day_num out of range for key={s.show_key}: {s.day_num}"
        assert s.duration_seconds > 0, f"duration_seconds is 0 for key={s.show_key}"


@pytest.mark.live
def test_schedule_getshows_start_time_conversion():
    """
    Verify sh_shour → HHMMSS conversion.

    The schedule scraper currently infers time from CSS pixel positions.
    This confirms we can replace that with the API value directly.
    """
    shows = get_all_shows()
    print("\n  Sample start times (sh_shour → HHMMSS):")
    for s in shows[:10]:
        h = s.start_seconds // 3600
        m = (s.start_seconds % 3600) // 60
        print(f"    {s.show_key:20s}  {s.start_seconds:6d}s  →  {s.start_time_str}  ({h}:{m:02d})")

    # Every start time should produce a valid HHMMSS string
    for s in shows:
        t = s.start_time_str
        assert len(t) == 6, f"start_time_str wrong length for {s.show_key}: {t!r}"
        h = int(t[:2])
        assert 0 <= h <= 23, f"Hour out of range for {s.show_key}: {t!r}"


@pytest.mark.live
def test_schedule_getshows_duration_vs_scraper():
    """
    The CSS scraper rounds duration to the nearest 30 min (by pixel height).
    The API gives exact seconds. Document how they compare for a few shows.
    """
    shows = get_all_shows()
    print("\n  Duration comparison (API seconds → minutes, vs scraper 30-min rounding):")
    for s in shows[:10]:
        api_min = s.duration_minutes
        rounded = max(30, round(api_min / 30) * 30)
        match = "✓" if api_min == rounded else f"← scraper would say {rounded} min"
        print(f"    {s.show_key:20s}  {s.duration_seconds}s = {api_min} min  {match}")


# ---------------------------------------------------------------------------
# Schedule API — altids
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_schedule_altids_shape():
    """
    Document what altids actually returns.

    The PHP client has a bug (double-unserialize) that suggests this endpoint
    may behave differently with json=1. We need to see the raw shape.
    Q2: Does it cover more shows than getshows (i.e., does it include archived-only shows)?
    """
    altids = get_show_altids()
    shows  = get_all_shows()
    scheduled_keys = {s.show_key for s in shows}

    print(f"\n  altids count:    {len(altids)}")
    print(f"  getshows count:  {len(scheduled_keys)}")

    in_altids_only   = set(altids) - scheduled_keys
    in_schedule_only = scheduled_keys - set(altids)

    print(f"  In altids but NOT in getshows ({len(in_altids_only)}): {sorted(in_altids_only)[:20]}")
    print(f"  In getshows but NOT in altids ({len(in_schedule_only)}): {sorted(in_schedule_only)[:20]}")

    assert isinstance(altids, list), "altids should return a list"
    # We don't assert coverage here — the print output answers Q2


# ---------------------------------------------------------------------------
# Schedule API — getgone
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_schedule_getgone_shape():
    """
    Document the raw shape of getgone.

    Q3: Is it usable for auto-updating Show.is_gone?
    We need to know if sh_altid is present and if there's enough info to
    match gone shows to existing DB records.
    """
    gone = get_gone_shows()

    print(f"\n  getgone count: {len(gone)}")
    if gone:
        print("  First entry keys:", list(gone[0].keys()))
        print("  First entry sample:")
        for k, v in gone[0].items():
            print(f"    {k}: {v!r}")

        # Check what fraction have sh_altid
        with_altid = [g for g in gone if g.get("sh_altid")]
        print(f"\n  Entries with sh_altid: {len(with_altid)}/{len(gone)}")

        if with_altid:
            print("  Sample altids:", [g["sh_altid"] for g in with_altid[:10]])

    # Non-fatal — we just want the shape documented
    assert isinstance(gone, list)


# ---------------------------------------------------------------------------
# Archive API — baseline
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_archive_known_show_returns_entries():
    """
    Baseline: the known-good show key returns entries with required fields.
    If this fails, the archive API itself is unreachable or the key changed.
    """
    entries = get_archive_entries(KNOWN_GOOD_KEY, num=5)

    print(f"\n  {KNOWN_GOOD_KEY}: {len(entries)} entries")
    for e in entries:
        print(f"    {e.air_datetime.date()}  expires={e.expires_datetime}  "
              f"lsecs={e.duration_seconds}  url={e.mp3_url[-40:]}")

    assert len(entries) > 0, f"No archive entries for known-good key: {KNOWN_GOOD_KEY}"
    for e in entries:
        assert e.mp3_url.startswith("http"), f"mp3_url looks wrong: {e.mp3_url}"
        assert e.air_timestamp > 0
        assert e.show_key  # idkey should echo back


@pytest.mark.live
def test_archive_entry_field_reliability():
    """
    Q5: Are expires and lsecs reliably present?

    Checks a few active shows and reports what fraction of entries have each
    field populated. This tells us how safely we can depend on them for
    expiry-sorted queuing and duration validation.
    """
    shows = get_all_shows()
    # Sample up to 5 shows that are likely to have recent archives
    sample_keys = [s.show_key for s in shows[:5]]

    total = 0
    has_expires = 0
    has_lsecs   = 0
    has_both    = 0

    print("\n  Field presence per show:")
    for key in sample_keys:
        entries = get_archive_entries(key, num=10)
        e_count  = sum(1 for e in entries if e.expires_timestamp)
        l_count  = sum(1 for e in entries if e.duration_seconds)
        b_count  = sum(1 for e in entries if e.expires_timestamp and e.duration_seconds)
        print(f"    {key:20s}  n={len(entries)}  "
              f"expires={e_count}/{len(entries)}  "
              f"lsecs={l_count}/{len(entries)}  "
              f"both={b_count}/{len(entries)}")
        total       += len(entries)
        has_expires += e_count
        has_lsecs   += l_count
        has_both    += b_count

    if total:
        print(f"\n  Overall: {total} entries — "
              f"expires {has_expires/total:.0%}, "
              f"lsecs {has_lsecs/total:.0%}, "
              f"both {has_both/total:.0%}")


# ---------------------------------------------------------------------------
# Q1 — slug consistency between schedule and archive APIs
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_slug_consistency_schedule_to_archive():
    """
    Q1: Do sh_altid slugs from getshows work in the archive API?

    Takes the first 10 scheduled shows, tries each in the archive API,
    and reports which ones return entries. A slug that returns nothing
    could mean: no recent archives, wrong slug mapping, or a mismatch
    between the two systems.
    """
    shows = get_all_shows()
    sample = shows[:10]

    print("\n  Schedule → Archive slug check:")
    mismatches = []
    for s in sample:
        entries = get_archive_entries(s.show_key, num=3)
        status = f"{len(entries)} entries" if entries else "NO ENTRIES"
        print(f"    {s.show_key:20s}  ({s.display_name[:30]})  →  {status}")
        if not entries:
            mismatches.append(s.show_key)

    if mismatches:
        print(f"\n  Shows with no archive entries: {mismatches}")
        print("  (Could be no recent archives, not necessarily a slug mismatch)")

    # We don't fail on mismatches — the output documents the reality.
    # A 100% hit rate confirms slugs are consistent; anything less needs investigation.


# ---------------------------------------------------------------------------
# Q4 — archive API response for unknown / gone shows
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_archive_response_for_unknown_slug():
    """
    Q4a: What does the archive API return for a slug that definitely doesn't exist?

    Documents whether it's an empty list, null, an error response, or raises
    an HTTP error. This tells us whether we need special handling in scraper.py.
    """
    fake_key = "zzz_this_show_does_not_exist_zzz"
    entries = get_archive_entries(fake_key, num=5)

    print(f"\n  Archive response for fake key {fake_key!r}: {entries!r}")
    # The client returns [] on non-list responses — confirm that holds
    assert isinstance(entries, list)


@pytest.mark.live
def test_archive_response_for_gone_show():
    """
    Q4b: What does the archive API return for a show that's off-air?

    Uses a show from getgone (if available) to check whether past archives
    are still accessible after a show goes off-air.
    """
    gone = get_gone_shows()
    if not gone:
        pytest.skip("getgone returned no results — cannot test gone show archive access")

    gone_with_key = [g for g in gone if g.get("sh_altid")]
    if not gone_with_key:
        pytest.skip("No gone shows have sh_altid — check getgone shape test")

    test_show = gone_with_key[0]
    key = test_show["sh_altid"]
    name = test_show.get("sh_name", "unknown")

    entries = get_archive_entries(key, num=5)
    print(f"\n  Gone show: {key!r} ({name})")
    print(f"  Archive entries returned: {len(entries)}")
    for e in entries:
        print(f"    {e.air_datetime.date()}  expires={e.expires_datetime}  lsecs={e.duration_seconds}")

    assert isinstance(entries, list)
    # Print tells us if archives persist after a show ends — important for backlog downloads


# ---------------------------------------------------------------------------
# get_show_by_key — single show lookup
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_get_show_by_key_known_show():
    """Verify single-show lookup returns consistent data with getshows."""
    all_shows   = get_all_shows()
    from_list   = next((s for s in all_shows if s.show_key == KNOWN_GOOD_KEY), None)
    from_single = get_show_by_key(KNOWN_GOOD_KEY)

    print(f"\n  From getshows: {from_list}")
    print(f"  From key lookup: {from_single}")

    assert from_single is not None, f"key lookup returned None for {KNOWN_GOOD_KEY}"
    assert from_single.show_key == KNOWN_GOOD_KEY

    if from_list:
        # start_seconds and duration should match between the two endpoints
        assert from_single.start_seconds == from_list.start_seconds, (
            f"start_seconds mismatch: getshows={from_list.start_seconds} "
            f"key={from_single.start_seconds}"
        )
        assert from_single.duration_seconds == from_list.duration_seconds, (
            f"duration_seconds mismatch: getshows={from_list.duration_seconds} "
            f"key={from_single.duration_seconds}"
        )
