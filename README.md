# ptown-perch-ops

Ops backend for Ptown Perch (ptownperch.com). Spec:
`docs/superpowers/specs/2026-08-15-str-ops-backend-design.md`.

## Phase 1 (current)
- Pulls the Airbnb iCal every 10 min; new/changed/canceled bookings and
  double-booking collisions are posted to Slack.
- Serves `/ical/{slug}.ics` for Airbnb to import (blocks direct-booking dates).

## Local dev
    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    .venv/bin/pytest tests/ -q
    .venv/bin/python -m scripts.seed_property perch "Ptown Perch" "<airbnb ical url>"
    .venv/bin/python -m app.sync.run           # one-shot sync
    .venv/bin/uvicorn app.main:app --reload    # API + scheduler

## Environment
- `DATABASE_URL` — Postgres on Railway (auto-rewritten to +asyncpg), sqlite locally
- `SLACK_WEBHOOK_URL` — incoming-webhook URL for the ops channel
- `SYNC_INTERVAL_MINUTES` — default 10; 0 disables the scheduler

## Deploy (Railway, dedicated project)
Deployed via Procfile (uvicorn web process; scheduler runs in-process).
After deploy: seed the property, then in Airbnb connect calendars both ways
(import `https://<app-domain>/ical/perch.ics`, export feeds the seed URL).
