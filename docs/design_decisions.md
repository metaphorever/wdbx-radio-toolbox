# WDBX Radio Toolbox — Design Decisions & Rationale
*Companion document to Dev Plan v2.0*

## How To Read This Document

Decisions are attributed to one of four sources:
- **Operator** — do not deviate without explicit approval
- **Architect** — Claude's recommendation, operator deferred
- **Joint** — emerged from dialogue
- **Open** — not yet resolved

Confidence levels: **Hard Requirement**, **Strong Recommendation**, **Provisional**, **Open Question**

---

## 1. Problem Scope

| ID | Decision | Source | Confidence |
|----|----------|--------|------------|
| PS-01 | Build a unified toolbox, not one-off scripts | Operator | Hard Requirement |
| PS-02 | Two primary modules: Archive Manager and Show Processor, plus shared Library/Settings | Joint | Strong Recommendation |
| PS-03 | Unified dashboard UI launching dedicated tools | Operator | Hard Requirement |
| PS-04 | Web-based UI accessible over LAN, not a desktop app | Operator | Hard Requirement |
| PS-05 | Mobile-responsive screening queue is a real requirement, not a stretch goal | Joint | Strong Recommendation |
| PS-06 | Budget is $0 — all dependencies must be free and open source | Operator | Hard Requirement |

---

## 2. Infrastructure

| ID | Decision | Source | Confidence |
|----|----------|--------|------------|
| IN-01 | Primary service host is one of the Ubuntu streaming PCs | Joint | Provisional |
| IN-02 | Fix NAS mount using systemd automount unit as Phase 0 task | Architect | Strong Recommendation |
| IN-03 | Downloads route to local staging queue if NAS is unreachable | Joint | Hard Requirement |
| IN-04 | Email via SMTP is the notification channel | Joint | Strong Recommendation |
| IN-05 | NAS capability for running Python services is an open question (Iomega StorCenter px6-300d) | Open | Open Question |
| IN-06 | AI processing runs only on M4 Mac mini or donated server, never blocking | Joint | Hard Requirement |

---

## 3. Architecture

| ID | Decision | Source | Confidence |
|----|----------|--------|------------|
| AR-01 | Python 3.11+ | Architect | Strong Recommendation |
| AR-02 | FastAPI + Uvicorn | Architect | Strong Recommendation |
| AR-03 | HTMX + Alpine.js + Tailwind CSS (no build step, no Node in production) | Architect | Strong Recommendation |
| AR-04 | SQLite via SQLModel | Architect | Strong Recommendation |
| AR-05 | chromaprint / fpcalc for audio fingerprinting | Architect | Strong Recommendation |
| AR-06 | silero-VAD for voice activity detection | Architect | Strong Recommendation |
| AR-07 | Whisper.cpp (or Apple MLX Whisper on M4) for transcription | Architect | Provisional |
| AR-08 | APScheduler for background job scheduling | Architect | Strong Recommendation |
| AR-09 | archive.wdbx.org listing page is the primary data source | Joint | Hard Requirement |
| AR-10 | Scrape-first, API-later architecture | Joint | Strong Recommendation |
| AR-11 | Monorepo layout: /archive_manager, /processor, /library, /web, /shared, /tests | Architect | Strong Recommendation |
| AR-12 | Single config.yaml with sane defaults — no hardcoded paths | Architect | Strong Recommendation |

---

## 4. Business Logic

| ID | Decision | Source | Confidence |
|----|----------|--------|------------|
| BL-01 | archive_enabled = false means zero DB records, zero download jobs | Operator | Hard Requirement |
| BL-02 | Schedule and archive page are the authoritative source of truth | Operator | Hard Requirement |
| BL-03 | Expected runtime derived from schedule slot, always a multiple of 30 minutes | Operator | Hard Requirement |
| BL-04 | Raw archive recordings include ~7 seconds of overlap — strip before processing | Operator | Hard Requirement |
| BL-05 | Fragment detection via archive listing, not URL construction | Joint | Hard Requirement |
| BL-06 | All fragments are downloaded, concatenated, and flagged as fragmented_source | Operator | Hard Requirement |
| BL-07 | Days to Stay is a dual signal: download urgency AND evergreen likelihood | Joint | Hard Requirement |
| BL-08 | TBA/overnight slots are excluded from archiving by default | Joint | Strong Recommendation |
| BL-09 | Talk programming is the least evergreen content as a rule | Operator | Hard Requirement |
| BL-10 | Shows with permanent or long retention are lowest evergreen priority | Operator | Hard Requirement |
| BL-11 | Target output runtime is exactly 2:00:00 (or the show's scheduled slot duration) | Operator | Hard Requirement |
| BL-12 | EAS alert detection is the only auto-reject signal | Joint | Strong Recommendation |

---

## 5. Fingerprint & Classification

| ID | Decision | Source | Confidence |
|----|----------|--------|------------|
| FC-01 | Three-tier classification: cross-show = underwriting; single-show high-freq = theme/bit/promo; rare = leave alone | Joint | Strong Recommendation |
| FC-02 | Per-show segment overrides take precedence over global classification | Joint | Hard Requirement |
| FC-03 | Automatic safelist derivation from fingerprint recurrence, not from DJ-provided files | Operator | Hard Requirement |
| FC-04 | Sub-2-minute recurring single-show segments are ambiguous and require human classification | Joint | Strong Recommendation |
| FC-05 | The bulk ingestion pass is a classification discovery exercise, not just deduplication | Joint | Strong Recommendation |
| FC-06 | Bulk classification UI presents patterns as clusters for fast human review | Joint | Strong Recommendation |
| FC-07 | All automated classifications are provisional until confirmed by a human at least once | Operator | Hard Requirement |

---

## 6. UX & Workflow

| ID | Decision | Source | Confidence |
|----|----------|--------|------------|
| UX-01 | Station manager onboarding wizard runs before any download jobs | Joint | Strong Recommendation |
| UX-02 | DJ self-screening is a first-class use case for the mobile queue | Joint | Strong Recommendation |
| UX-03 | Lightweight reviewer identity (name/handle per session, not full authentication) | Architect | Strong Recommendation |
| UX-04 | Screening queue sorted by Evergreen Score descending | Architect | Strong Recommendation |
| UX-05 | Two-tier padding music library: general pool plus optional show-specific pools | Operator | Strong Recommendation |
| UX-06 | Station ID insertion uses random-weighted selection to ensure variety | Architect | Strong Recommendation |
| UX-07 | Waveform viewer shows talk/music regions, underwriting markers, and EAS flags color-coded | Architect | Strong Recommendation |

---

## 7. Known Exceptions & Institutional Knowledge

| Show | Note |
|------|------|
| Time Capsule | Legitimately 3 hours. `expected_duration_override = 180`. |
| Friday 4-hour block | Legitimate 4-hour block. `expected_duration_override = 240`. Confirm show name. |
| Sunday Gospel / Wakin' Up With Porter | Longer runtime. Set override during onboarding. |
| Opera / Live-Only Programming | Never appears on archive page. No action needed. |
| Carbondale Crossroads | 'Perm' retention, station-produced/public-domain candidate. Shows fragmentation (Sept 26, July 18, Aug 22). |
| Democracy Now! | Syndicated talk. Long retention (46–59 days). Lowest evergreen priority. Time-specific news. |
| TBA OPEN / Overnight Slots | Destination slots for processed reruns. Default `archive_enabled = false`. |
| Electric Blues Hours | DJ plays their own promos as interstitials. Per-show override: keep on home show, remove on others. Canonical override example. |

---

## 8. Open Questions — Pending Research

| ID | Question | Status |
|----|----------|--------|
| Q1 | NAS Capability: Does Iomega StorCenter px6-300d support persistent Python service? | Open |
| Q2 | Pacifica API: Check DevTools Network tab at archive.wdbx.org for undocumented JSON API | Open |
| Q3 | MP3 URL structure | **Resolved 2026-03-23**: `wdbx_{YYMMDD}_{HHMMSS}{slug}.mp3` — no underscore between time and slug. Example: `wdbx_260205_070000islandreport.mp3` |
| Q4 | Padding Music Library: Does station manager have existing licensed/owned music? | Open |
| Q5 | Existing Python Script | **Resolved**: `reference/dl-toggle.py` + `reference/showst.txt` added to repo |
| Q6 | SMTP credentials / Pacifica documentation | Open |

*Update this table as questions are resolved. Bump doc to v1.1 when all are closed.*
