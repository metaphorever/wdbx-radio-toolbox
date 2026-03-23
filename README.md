# WDBX Radio Toolbox

Community radio archive & processing suite for WDBX (Pacifica LA affiliate).

Automates show archiving, fingerprint-based audio cleaning, and overnight programming generation from a web UI accessible over LAN.

## Modules

| Module | Status | Phase |
|--------|--------|-------|
| Archive Manager | Planned | 1–2 |
| Show Processor | Planned | 5–7 |
| Library & Settings | Planned | 3 |
| Onboarding Wizard | Planned | 0.5 |

## Stack

- Python 3.11+ / FastAPI + Uvicorn
- HTMX + Alpine.js + Tailwind CSS (no build step, no Node in production)
- SQLite via SQLModel
- ffmpeg + pydub + librosa + chromaprint / fpcalc
- silero-VAD, Whisper.cpp (optional / M4 only)
- APScheduler for background jobs

## Setup

```bash
pip install -r requirements.txt
cp config.yaml config.local.yaml   # edit paths and credentials
uvicorn web.main:app --host 0.0.0.0 --port 8000
```

> `config.local.yaml` is gitignored. Never commit credentials.

## Running Tests

```bash
pytest tests/
```

## Project Structure

```
archive_manager/   Download engine, schedule scraper, NAS handler
processor/         EAS detection, fingerprinting, screening queue, audio pipeline
library/           Underwriting bank, safelist, padding, station IDs
web/               FastAPI app, templates, routes
shared/            Config loader, SQLModel engine, data models
tests/             Test suite (URL assembly, fingerprinting, duration validation)
docs/              Design decisions, dev plan, stretch goals
reference/         Existing production scripts (read-only reference)
config.yaml        All configurable paths and settings
```

## Documentation

- [`docs/dev_plan_v2.md`](docs/dev_plan_v2.md) — full development plan and phased roadmap
- [`docs/design_decisions.md`](docs/design_decisions.md) — every significant decision with rationale
- [`docs/stretch_goals.md`](docs/stretch_goals.md) — future ideas parking lot

## Reference

[`reference/dl-toggle.py`](reference/dl-toggle.py) — existing cron-based download script (production as of March 2026).
[`reference/showst.txt`](reference/showst.txt) — show schedule file used by the existing script.

*Built for the community.*
