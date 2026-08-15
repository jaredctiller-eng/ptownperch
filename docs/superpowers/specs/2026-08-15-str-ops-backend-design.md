# Ptown Perch — STR Ops Backend Design

**Date:** 2026-08-15
**Status:** Approved by Jared (design conversation, 2026-08-15)
**Scope:** Single-tenant operations backend for our own short-term rental portfolio (1 unit today — the Provincetown condo at ptownperch.com — designed to handle a handful).

## Summary

A lean backend that (1) keeps availability in sync between Airbnb and direct bookings autonomously, and (2) turns every other operational action — cleaning tasks, guest messages, pricing changes, direct-booking approvals — into a *suggestion* delivered to Slack with Approve/Reject buttons. Humans decide; the system drafts, schedules, and executes approved actions.

No payment processing in v1: direct bookings are inquiry-based, paid manually via Venmo/Zelle. No channel-manager subscription: Airbnb integration is iCal-only, with the integration behind an adapter so a channel manager can replace it later without rearchitecting.

## Decisions made (with rationale)

| Decision | Choice | Rationale |
|---|---|---|
| Tenancy / scale | Single-tenant, own properties (1–20, currently 1) | No owner statements, no multi-tenant auth |
| Airbnb integration | iCal only | Free; channel-manager APIs cost ~$10–50/property/mo. Accepted trade-off: no rate push, no Airbnb messaging API, sync lag |
| Autonomy model | Calendar sync acts autonomously; everything else is suggestion + human approval | Operator explicitly wants approve-before-act |
| Approval channel | Slack (interactive buttons); admin dashboard for richer work | iMessage has no official API; Slack bots are first-class |
| Direct booking | Inquiry → approve → hold → manual payment (Venmo/Zelle) → confirmed | Zero payment-processing cost/risk in v1; fits approval model; removes instant-confirmation double-booking risk |
| Stack | FastAPI + Postgres + APScheduler on Railway | Operator's existing stack and hosting familiarity |
| Infrastructure | **Dedicated Railway project** (separate from any existing projects) | Isolation from unrelated infra |

## Accepted risks and operator responsibilities

- **iCal sync lag:** Airbnb refreshes imported calendars on its own schedule (minutes to hours). A collision between an Airbnb booking and a direct hold is possible in that window. Mitigations: 10-minute inbound polling, holds reflected in outbound iCal immediately, collision detector fires an urgent Slack alert on every sync. Cannot be driven to zero without a channel manager.
- **Manual payments:** Venmo/Zelle offer no chargeback protection for guests and no automated refunds. Cancellation terms must be stated in the payment-instructions email.
- **Lodging tax:** Massachusetts room-occupancy tax applies to direct bookings regardless of payment method. Airbnb collects/remits for Airbnb bookings; direct bookings are our responsibility. **Verify current state + local Provincetown rates and registration before accepting the first direct booking.** The quote engine must support a configurable tax line.
- **Airbnb messaging:** iCal provides dates only. Messages to Airbnb guests are drafted for copy-paste, never sent programmatically.

## Architecture

One FastAPI + Postgres service on a dedicated Railway project, with APScheduler running in-process for periodic jobs. Three surfaces:

1. **Public API** — called by ptownperch.com: availability calendar, quote, "request to book" form submission. No auth beyond rate limiting + basic abuse protection.
2. **Slack bot** — delivers suggestions/alerts with interactive buttons; handles Approve / Decline / Mark-paid actions.
3. **Admin dashboard** — private, server-rendered (FastAPI + Jinja + htmx; no separate frontend build): month calendar (color-coded by source), reservations, pending suggestions, tasks, rate-rule editor, message-template editor. HTTP basic auth for the two hosts in v1 (magic-link login is a later nicety).

### Data model (core tables)

- `properties` — one row per unit; slug, timezone, Airbnb iCal URL, tax config, cleaner contact.
- `reservations` — `source` (`airbnb_ical` | `direct`), `status` (`inquiry → hold → confirmed → checked_out → canceled`), guest name/email/phone, check-in/out, quote breakdown (nightly, fees, tax), `paid` flag, hold-expiry timestamp.
- `calendar_blocks` — manual blocks (maintenance, personal use).
- `suggestions` — the approval queue: `type`, JSON payload, `status` (`pending → approved | rejected | expired`), who acted, timestamps, Slack message ref. Every ops feature writes here; one dispatcher renders and routes all of them.
- `tasks` — cleaning/turnover, linked to reservations; date, assignee, same-day-turnover flag, completion state.
- `message_templates` — per-event templates (confirmation, check-in T-2d, checkout reminder) with variable substitution.
- `rate_rules` — seasonal/weekend/min-stay/base rates driving direct-booking quotes and pricing suggestions.

### Calendar sync (the one autonomous loop)

Every 10 minutes per property:

1. Fetch Airbnb's iCal export; upsert reservations/blocks (stable UID matching).
2. Diff against previous state: new booking → Slack notification + cleaning-task suggestion; cancellation → Slack alert.
3. Run collision detector: any overlap between Airbnb events and direct `hold`/`confirmed` reservations → urgent Slack alert.

Outbound: serve `/ical/{property}.ics` containing confirmed **and held** direct reservations plus manual blocks; Airbnb imports it to block those dates.

The Airbnb integration sits behind a `ChannelAdapter` interface (`pull_events()`, `push_availability()` as a no-op for iCal) so a channel-manager API can replace it later.

### Direct booking flow

1. Site posts to public API: dates, guest count, name, email/phone.
2. Backend validates availability (against all sources), computes quote from `rate_rules` + tax config, creates reservation in `inquiry`.
3. Slack message with quote + **Approve / Decline** buttons.
4. **Approve** → status `hold`; dates appear in outbound iCal immediately; guest receives email (via Resend/Postmark) with quote, house rules, cancellation terms, and Venmo/Zelle payment instructions.
5. **Mark paid** (Slack button or dashboard) → `confirmed`; cleaning task scheduled; message sequence starts.
6. Holds auto-expire after a configurable window (default 48h), with a Slack heads-up before release.

### Suggestion pipeline

Producers → `suggestions` table → single Slack dispatcher → executor on approval.

- **Cleaning/turnover:** checkout detected (either source) → suggest task with date and same-day-turnover flag → approve → task created, email notification to cleaner (SMS via Twilio is a later add-on, not in v1).
- **Messaging:** direct guests — drafted emails at lifecycle points, approve-to-send. Airbnb guests — drafted text presented for copy-paste into the Airbnb app.
- **Pricing:** periodic engine evaluates `rate_rules` vs. calendar (occupancy, weekends, holidays, lead time) → suggests changes; approve → updates direct-quote rules and provides exact numbers to paste into Airbnb.

### Failure posture

If the app is down: Airbnb operates normally; the site's booking form fails visibly; outbound iCal goes stale (Airbnb keeps last-known blocks). Nothing silently double-books. All state lives in Postgres — containers are ephemeral.

## Build order (each phase independently useful)

1. Calendar sync (inbound iCal, diffing, Slack notifications, collision detector) + outbound iCal.
2. Direct booking: public API (availability/quote/inquiry), Slack approve/decline/mark-paid, hold expiry, guest emails.
3. Cleaning/turnover tasks.
4. Messaging templates + lifecycle sequences.
5. Pricing suggestion engine.
6. Dashboard polish (a minimal dashboard lands with phase 2).

## Non-goals (v1)

- Online payment processing (Stripe) — revisit when direct volume justifies it.
- Channel-manager integration, Airbnb rate push, programmatic Airbnb messaging.
- Multi-tenancy, owner statements, dynamic-pricing ML. Rate rules are deterministic.
- Replacing or restyling ptownperch.com — the site only gains a booking-request form calling the public API.
