# WDBX Radio Toolbox — Development Plan & Roadmap v2.0
*Community Radio Archive & Processing Suite*

---

## 1. Problem Statement

WDBX is a community radio station streaming via Pacifica LA infrastructure. Archived shows are deleted from Pacifica servers after 14 days (with exceptions for talk/public-domain content). The workflow to collect, screen, clean, and repurpose content is entirely manual and bottlenecked.

### 1.1 The Archiving Problem
- Schedule changes require manual script updates — new shows are silently missed and lost forever after 14 days
- Current script only downloads the primary segment; restart fragments from stream glitches are never captured
- No alerting when downloads fail or the NAS is unreachable
- No deduplication — years of fragmented, overlapping archives exist
- SMB mount to NAS fails silently after power outages

### 1.2 The Processing Problem
- Underwriting and promos (50–100 known files plus long tail of baked-in content) must be manually identified and cut in a DAW
- Post-removal runtime no longer matches target — padding must be manually added
- No systematic screening for dealbreakers: EAS alerts, glitched audio, time-sensitive talk
- No tracking of which shows have been reviewed
- DJ theme songs must be preserved — no system to safelist them automatically
- No systematic way to distinguish evergreen from time-specific programming

### 1.3 Constraints
- Donated legacy hardware — no GPU except operator's personal M4 Mac mini (not a continuous resource)
- Tech-literate but not power user operators
- Budget: $0
- Station manager on Windows 11; streaming PCs on Ubuntu; all access over LAN

---

## 2. System Overview

Three major modules sharing a common database and NAS storage layer, running as a persistent service on one of the Ubuntu streaming PCs.

- **MODULE 1 — Archive Manager:** Automated show downloader with schedule tracking, fragment reassembly, NAS health monitoring, failsafe local queue
- **MODULE 2 — Show Processor:** Fingerprint analysis, bulk classification UI, screening queue, underwriting removal, padding, final output generation
- **MODULE 3 — Library & Settings:** Underwriting bank, auto-derived safelist, per-show configuration, padding music library, station ID pool, system config

### 2.1 Architecture Principles
- Web-first UI at a LAN IP, accessible from any browser including Windows 11 and DJs' mobile phones
- archive.wdbx.org is the source of truth — scraper parses full listing to discover all files including restart fragments; URL construction is fallback only
- AI processing is optional accelerator, not a dependency — every workflow has a CPU-only fallback
- NAS is primary storage but never a single point of failure
- Everything tracked in SQLite — no show processed twice without intent
- `archive_enabled = false` means no download job ever created, no record enters the system, no exceptions

### 2.2 Technology Stack

| Layer | Choice |
|-------|--------|
| Runtime | Python 3.11+ |
| Web Framework | FastAPI + Uvicorn |
| Frontend | HTMX + Alpine.js + Tailwind CSS (no build step) |
| Database | SQLite via SQLModel |
| Audio Processing | ffmpeg + pydub + librosa |
| Audio Fingerprinting | chromaprint / fpcalc |
| Speech Detection | silero-VAD |
| Transcription (optional) | Whisper.cpp / Apple MLX Whisper |
| Task Scheduling | APScheduler + threading |
| Notifications | Python smtplib (SMTP) |
| NAS Mount | systemd automount unit |

---

## 3. Key Signals & Classification Logic

### 3.1 Archive Page Signals (Pre-Download)

| Signal | Meaning |
|--------|---------|
| Days to Stay 0–2 | Urgent grab |
| Days to Stay 13–14 | Standard music show |
| Days to Stay 30–59 | Syndicated talk |
| Days to Stay = Permanent | Station talk / public domain |
| Duration = 0:00:00 | Not yet recorded — recheck +2 hours |
| Duration > scheduled slot | Stream glitch fragments — grab all, reassemble, flag `fragmented_source` |
| Duration significantly < scheduled slot | Partial recording — download, flag `suspect_quality` |
| TBA/OPEN/overnight in name | Destination slot — default `archive_enabled = false` |

### 3.2 Runtime & Duration Rules
- Expected runtime derived from schedule slot (always a clean multiple of 30 minutes)
- Known exceptions stored as `expected_duration_override`
- Raw recordings include ~7 sec overlap — stripped during processing, not a duration anomaly
- Acceptable raw duration range: scheduled slot ± 30 seconds

### 3.3 Fingerprint Classification

| Pattern | Classification |
|---------|---------------|
| Cross-show, short duration | Underwriting/promo/PSA — propose add to removal bank |
| Single-show only, high frequency | Theme/bit/promo — flag for human classification |
| Single-show, low frequency, >~90 sec | Theme song/intro/outro — propose auto-safelist |
| Appears on home show and others | DJ's own promo — per-show config: keep on home, remove on others |
| Appears rarely or once | Unique content — leave alone |
| Short, appears on all shows | Station ID — add to station ID pool |

### 3.4 Evergreen Scoring (0–100)
Composite weighted score from: retention duration, talk/music ratio, show classification preset, audio quality flags, fragment flag, schedule slot name, and optionally Whisper transcript. Talk programming = least evergreen. Permanent/long retention = strong negative signal.

---

## 4. Module Specifications

### 4.1 Archive Manager

**Schedule Tracker:** Scrapes archive.wdbx.org every 6 hours; diffs against SQLite; emails digest of changes; schedule grid UI with per-show toggles.

**Fragment-Aware Download Engine (KEY v2 change):** Scraper queries archive listing for ALL files matching a given show key + date. Fragment detection = finding more than one file. All fragments downloaded, concatenated in chronological order via ffmpeg, originals preserved in `/fragments` subdirectory, concatenated file tagged `fragmented_source = true`. Retry: 3 attempts with exponential backoff.

**NAS Handler & Failsafe:** systemd automount unit; lightweight write probe before each job; local staging queue if NAS unreachable; persistent red banner in UI; one-click 'Copy to NAS'.

Folder structure: `/radio-archive/{year}/{show-slug}/{YYMMDD}_{HHMMSS}_{showname}.mp3`

### 4.2 Station Manager Onboarding Wizard (Phase 0.5)
Runs once before any download jobs (~30–45 min). Steps: show review with auto-detected suggestions; exception runtime registration; hard excludes; overnight/TBA slot confirmation. All decisions logged with `confirmed_by_manager = true`.

### 4.3 Show Processor

**Bulk Ingestion & Pattern Classification:** One-time archaeological pass over existing archive. All files fingerprinted and indexed. Deduplication UI. Pattern surfaces presented as grouped clusters for bulk human review.

**Automated Pre-Screening Scorecard:** EAS detection (FFT, 1050 Hz + 853 Hz), audio quality checks, talk/music ratio via silero-VAD, underwriting timestamp matching, runtime validation, fragment flag, Evergreen Score.

**Human Screening Queue:** Sorted by Evergreen Score descending. Queue card shows: show name, air date, duration, Evergreen Score badge, quality flag icons, talk ratio bar, fragment warning. Waveform thumbnail with color-coded regions. Audio player with skip-to-flag controls. Decision buttons: APPROVE / APPROVE WITH NOTES / NEEDS REVIEW / REJECT with keyboard shortcuts. Lightweight reviewer identity (name/handle per session). Mobile-optimized layout. Queue state fully persisted.

**Audio Processing Pipeline (non-destructive):** Overlap strip → underwriting removal via ffmpeg → safelist protection → second-pass heuristic for unmatched segments → new station IDs (random-weighted) → padding from library with genre/mood tags and crossfades → final assembly with ID3 tags → output to `/nas/overnight-programming/{date}_{showname}_processed.mp3`

### 4.4 Library & Settings Module
Underwriting Bank, Per-Show Segment Config, Padding Music Library (two-tier: general + show-specific), Station ID Pool (usage tracking), Show Configuration Registry, System Settings, Audit Log.

---

## 5. Data Model (SQLite / SQLModel)

See `shared/models.py` for the full schema. Tables: `show`, `episode`, `analysisresult`, `segmentfingerprint`, `showsegmentoverride`, `screeningdecision`, `processedoutput`, `libraryasset`, `systemevent`.

---

## 6. Phased Development Roadmap

| Phase | Priority | Scope |
|-------|----------|-------|
| **0** | URGENT | Fix NAS mount (systemd automount); resolve Q1–Q3; project repo setup; confirm SMTP |
| **0.5** | URGENT | Station manager onboarding wizard |
| **1** | URGENT | Archive Manager MVP — scraper, fragment-aware download engine, NAS failsafe, email alerts, minimal schedule grid UI |
| **2** | HIGH | Archive Manager Complete — schedule change detection, bulk retroactive download, audit log UI, duration validation |
| **3** | HIGH | Library Module — underwriting bank, per-show segment overrides, station ID pool, two-tier padding library |
| **4** | HIGH | Bulk Ingestion & Classification — corpus fingerprint indexing, deduplication UI, pattern clustering |
| **5** | MEDIUM | Processor: Analysis — EAS detection, audio quality, talk ratio, underwriting matching, Evergreen Score, waveform thumbnails |
| **6** | MEDIUM | Processor: Screening UI — score-sorted queue, waveform viewer, audio player, mobile layout, DJ self-screening, keyboard shortcuts |
| **7** | MEDIUM | Processor: Output Pipeline — underwriting removal, station ID insertion, padding, final assembly, ID3 tagging, NAS output |
| **8** | STRETCH | AI Enrichment — Whisper transcription (async), enhanced evergreen scoring, Pacifica API swap-in |

---

## 7. Handoff Notes for Implementing Agents

### Environment
- Ubuntu 22.04 LTS on Dell ~2015 PC
- Iomega StorCenter px6-300d NAS (systemd automount, Phase 0)
- M4 Mac mini (async AI only, not always available)
- Windows 11 station manager over LAN
- Various mobile devices for DJs

### Critical Implementation Rules
1. Archive listing is the primary data source — never blindly construct a single URL
2. `archive_enabled = false` = zero DB records, zero jobs — clean absence
3. Overlap constant (~7 sec) is stripped before validation, not a quality flag
4. Every automated classification is provisional until `confirmed_by` a human — UI must show this
5. Per-show overrides take precedence over global classification
6. All background jobs must be resumable after crash — idempotent, status tracked in SQLite
7. No hardcoded paths — all paths from `config.yaml`

### Code Standards
- Monorepo layout (this repo)
- Single `config.yaml` (no hardcoded paths)
- Rotating file log + web UI event log
- Pinned `requirements.txt` (lock after first install)
- Tests for URL/path assembly, fingerprint matching, and duration validation are non-negotiable from day one

*Built for the community. — WDBX Radio Toolbox Development Plan v2.0*
