# ptown-perch-ops

Ops backend for Ptown Perch (ptownperch.com). Spec:
`docs/superpowers/specs/2026-08-15-str-ops-backend-design.md`.

## Phase 1
- Pulls the Airbnb iCal every 10 min; new/changed/canceled bookings and
  double-booking collisions are posted to Slack.
- Serves `/ical/{slug}.ics` for Airbnb to import (blocks direct-booking dates).
- Admin API (`/admin/*`, HTTP Basic auth) for manually recording, listing, and
  canceling direct bookings — no signed contract, used for overrides.

## Phase 2 (current)
- Public guest inquiry page (`/book`) quotes a price (Airbnb rate minus 14%,
  tax gated off by default) and posts an approval request to Slack.
- Approving in Slack auto-emails the guest a link (Gmail SMTP) to a page
  where they sign the rental agreement (draw-pad signature) and see payment
  instructions.
- `mark-paid` (Slack "Mark Paid" button, posted when the guest signs, or the
  admin API) requires a signature on file before confirming — never confirms
  an unsigned booking.
- Unpaid holds auto-expire after 48h (warned at 36h).
- Schema is Alembic-managed from this phase onward — see "Deploy" below.

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
- `SLACK_SIGNING_SECRET` — Perch Ops Slack app's signing secret, for verifying
  interactive button payloads
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` — sends the one automated guest email
  (booking link); needs 2-Step Verification enabled on the Gmail account to
  generate an app password
- `PUBLIC_BASE_URL` — this service's public URL, used to build guest booking
  links (e.g. `https://app-production-b8f3.up.railway.app`)
- `HOLD_EXPIRY_HOURS` / `HOLD_WARNING_HOURS` — default 48 / 36

## Deploy (Railway, dedicated project)
Deployed via Procfile (uvicorn web process; scheduler runs in-process).

**Migrations (from Phase 2 onward):** this repo uses Alembic. After merging a
migration, apply it to production yourself — it does not auto-run on deploy:
    export DATABASE_URL=<Postgres service's public proxy URL, not the
      .railway.internal one — alembic runs from your laptop, outside
      Railway's private network>
    .venv/bin/alembic upgrade head
The very first Alembic migration (`0001_baseline`) must be applied with
`alembic stamp 0001` instead of `upgrade head`, since production's tables
already existed (created by the old `create_all`) before Alembic was
introduced — `stamp` records the revision without re-running `CREATE TABLE`.
Every migration after that uses the normal `upgrade head`.

**Before real guests use `/book`:** set `nightly_rate_cents` via
`PATCH /admin/config`, and set the contract template via
`PUT /admin/contract-template` with every `[____]`-style blank filled in —
the guest page refuses to render while any remain. Check
`GET /admin/status`'s `contract_has_unresolved_blanks` field to verify
before sharing a booking link.
