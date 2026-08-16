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
- Quotes use per-night pricing (`PUT /admin/rates`, a bulk `{"YYYY-MM-DD": cents}`
  upload — e.g. paste a month's worth of Airbnb calendar prices at once) with
  `nightly_rate_cents` on `Property` as the fallback for any date without an
  explicit entry.

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

**Migrations:** the Railway service's `preDeployCommand` is set to
`alembic upgrade head`, so every deploy applies any pending migration
automatically before the app boots — no manual step needed for ordinary
migrations. `alembic upgrade head` is idempotent (a no-op when already
current), so this is safe to leave in place permanently.

The one exception was the very first migration (`0001_baseline`): production's
tables already existed (created by the old `create_all`) before Alembic was
introduced, so `0001` had to be applied once with `alembic stamp 0001`
instead of `upgrade head` — `stamp` records the revision without re-running
`CREATE TABLE`. That was a one-time bootstrap step, already done; every
migration since (`0002`, `0003`, ...) has applied automatically via the
pre-deploy command.

**Phase 1 setup (property + Airbnb connection), needed on a first-time
deploy — do this before the Phase 2 steps below:** seed the property with
`scripts.seed_property` (slug, name, and its Airbnb *export* calendar URL),
then in Airbnb connect calendars both ways — import this service's outbound
`https://<app-domain>/ical/{slug}.ics` feed, and export feeds the same URL
you seeded. The availability the guest booking page and iCal sync rely on
depends on the property existing and both calendar directions being
connected.

**Before real guests use `/book`:** set `nightly_rate_cents` via
`PATCH /admin/config`, and set the contract template via
`PUT /admin/contract-template` with every `[____]`-style blank filled in —
the guest page refuses to render while any remain. Check
`GET /admin/status`'s `contract_has_unresolved_blanks` field to verify
before sharing a booking link.
