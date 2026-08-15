# Ptown Perch — Phase 2: Direct Booking Flow Design

**Date:** 2026-08-15
**Status:** Approved by Jared (design conversation, 2026-08-15)
**Scope:** The guest-facing direct-booking lifecycle — inquiry, quote, Slack approve/decline, signed rental contract, manual payment, confirmation, hold expiry. Builds on Phase 1 (calendar sync, outbound iCal, Slack notifier, admin API), which is deployed and live.

## Summary

A guest requests to book directly (bypassing Airbnb) through a page we host. The system quotes a price, drafts a Slack approval request, and — once approved — auto-emails the guest a link to review the quote, read and sign a rental agreement, and see payment instructions. The operator confirms manually once Venmo/Zelle payment lands. The existing Phase 1 admin API is kept unchanged as a manual-override path for bookings that don't need this flow (e.g. comped stays, corrections).

## Decisions made (with rationale)

| Decision | Choice | Rationale |
|---|---|---|
| Pricing basis | Operator-maintained "current Airbnb nightly rate" config value, minus 14% | Airbnb exposes no live pricing API/feed at this account tier — the iCal feed is availability-only. 14% approximates Airbnb's guest service fee, so the guest pays less than booking on Airbnb while the operator nets about the same as an Airbnb payout. |
| Lodging tax | $0 on every quote and contract, both wired to a single config flag | The contract template includes a specific 14.45% combined rate (MA room occupancy + Provincetown local option + Cape Cod & Islands Water Protection Fund), but Provincetown STR registration is not yet confirmed active. Charging guests an unverified tax on a signed legal document is worse than temporarily charging none. Flip the flag once registration and the rate are confirmed. |
| Guest signature | Hand-drawn signature pad (canvas), stored as an image alongside typed name, timestamp, and IP | Chosen over typed-name-only (less legally weighty) and a third-party e-sign service (unnecessary cost/complexity at this scale). |
| Guest-facing pages | Hosted by this backend (`/book`, `/book/<token>`), not embedded in ptownperch.com | We don't have the ptownperch.com codebase in this project; a self-hosted page needs no changes there. |
| Contract storage | One editable text template with placeholders, seeded from the operator-provided draft, refuses to render while any `[____]`-style blank remains unfilled | Keeps the legal text out of code (editable without a redeploy) and makes it structurally impossible for a guest to sign a contract containing a placeholder. |
| Admin API (Phase 1) | Existing create/list/cancel endpoints kept as-is, unchanged | It already works and was used same-day to correct a real booking. Bookings entered this way carry no signed contract. (A new `mark-paid` admin action is added in this phase — see Architecture — but nothing existing changes.) |
| Approval delivery | Auto-emailed via Gmail SMTP (operator's real Gmail + app password) when Slack Approve is clicked | Reverses the earlier "guest emails stay manual" call for this one transactional message specifically. Later lifecycle emails (check-in instructions, checkout reminder) remain manual/out of scope for this phase. |
| Hold expiry | 48h, Slack warning before release (per original system spec) | Unimplemented in Phase 1; the admin API bypassed the hold state entirely. |

## Accepted risks and blockers before this can go live for a real guest

- **Contract blanks are not yet filled in.** STR registration number, MA DOR certificate number, deposit amount/approach, pet fee, parking description, cancellation-tier percentages, and occupancy limit are all still placeholders in the operator-provided draft. The system will refuse to serve `/book/<token>` while the active template contains any unfilled blank — this is enforced in code, not a manual checklist item. Filling them in is a data-entry task for the operator (via an admin endpoint, covered in the implementation plan), not a design decision.
- **Not legal advice.** This is a legally meaningful document (liability, indemnification, cancellation terms, no-tenancy clause). A Massachusetts attorney should review it before a real guest signs, independent of when the code ships.
- **Tax is $0 until registration is verified**, per the decision above — this must be flipped deliberately, not left forgotten. The implementation should make the current tax state visible (e.g., in `/admin/status`) so it isn't silently stale for months.
- **Gmail SMTP requires 2-Step Verification enabled** on the sending Gmail account before Google will issue an app password.
- **Slack Interactivity requires a signing secret.** The Perch Ops Slack app needs Interactivity turned on with a Request URL pointed at this backend, and the app's signing secret added as a new Railway env var, so incoming button-click payloads can be verified as genuinely from Slack.

## Architecture

### Lifecycle

1. **Inquiry** — Guest visits `/book` (a page we host), submits dates, party size, name, email, phone, home address, and occupant names. Backend validates availability (against all existing reservation sources, same overlap rule as Phase 1's admin API), computes a quote (`configured_nightly_rate × nights × 0.86`, tax fixed at $0), creates a `Reservation` (`source=direct`, `status=inquiry`), and posts to Slack with the quote and **Approve / Decline** buttons. The guest sees a plain "thanks, we'll be in touch" confirmation — no promise of a hold yet.
2. **Approve** (Slack interactive button) — Status → `hold`. Backend generates a random unique `booking_token`, builds the guest page URL, renders the guest email from a template (quote, contract link, payment instructions), and sends it via Gmail SMTP to the guest's email. Slack is updated to show what was sent. If the send fails, Slack shows the drafted email text instead so the operator can send it manually — never a silent failure.
3. **Decline** (Slack interactive button) — Status → `canceled`. Slack shows a drafted decline message for the operator to relay if desired.
4. **Guest reviews and signs** — At `/book/<token>`, the guest sees the quote breakdown, the fully-rendered contract (operator/property fields filled from config, guest fields filled from their inquiry answers), payment instructions, and a signature pad. Submitting stores `signed_name`, `signature_image`, `signed_at`, `signed_ip`, and a frozen `contract_snapshot` (the exact rendered text they saw) on the reservation. This does **not** change status — the reservation stays `hold`.
5. **Payment confirmation** — Guest pays via Venmo/Zelle per the instructions on the page. Once the operator sees the payment land, they trigger **Mark Paid** (Slack button or admin API). This is only permitted when `status == hold` and `signed_at is not null` — a signed contract is a hard prerequisite for confirming, enforced in code. Status → `confirmed`.
6. **Hold expiry** — A scheduled job (mirroring Phase 1's sync scheduler pattern) scans for `hold` reservations older than 48h with no `Mark Paid`, posts a Slack warning, and after a further grace window releases them (status → `canceled`), freeing the dates.

### Data model

Extends the existing `Reservation` table (no new table — see rationale in the earlier design conversation: the sync engine and outbound iCal route already treat `Reservation` as the single source of truth for anything that blocks dates, and splitting guest-initiated bookings into a second table would mean teaching collision detection about two tables for no real benefit).

New nullable columns: `booking_token` (random, unique, indexed — identifies the guest page without exposing the sequential `id`), `guest_home_address`, `occupant_names`, `signed_name`, `signature_image`, `signed_at`, `signed_ip`, `contract_snapshot`.

New config, stored in the database and editable via new admin endpoints (not env vars, since these change more often than Phase 1's per-deploy property config): operator-maintained `nightly_rate`, a `direct_booking_tax_enabled` flag (default false) alongside the fixed 14.45% rate for when it's flipped on, and the contract template text itself.

### Contract template

Stored as one row of editable text with `{{placeholder}}` interpolation for booking-specific fields (guest name, dates, computed price, occupants, home address). Seeded from the operator-provided draft verbatim, including its unresolved `[____]`-style blanks. Rendering for a real guest is blocked — with a clear operator-facing error, not a guest-facing 500 — while any such blank remains in the active template.

### Slack interactivity

The existing Perch Ops Slack app (used for Phase 1's webhook notifications) gains an Interactivity configuration: a Request URL on this backend that receives button-click payloads, verified via Slack's signing secret (HMAC over the raw body + timestamp, per Slack's standard verification scheme) before any action is taken. Approve/Decline/Mark Paid are all implemented as this one verified endpoint dispatching on the button's action id.

### Relationship to Phase 1's admin API

`POST /admin/reservations`, `GET /admin/reservations`, and cancel remain exactly as built in Phase 1 — entering a reservation directly, with no inquiry, no Slack approval step, no contract. This is the mechanism used earlier the same day to confirm the John Cena stay was already covered by an Airbnb-side block. Reservations entered this way have `signed_at IS NULL` and are indistinguishable from any other unsigned direct booking in the data — which is correct, since they weren't guest-signed. This phase adds new admin endpoints alongside the existing ones: `mark-paid` (the operator-side counterpart to Slack's Mark Paid button, same `hold` + signed precondition) and the rate/tax/contract-template config editors described above.

## Non-goals (this phase)

- Automated lifecycle emails beyond the one approval email (check-in instructions, checkout reminder) — still manual per the original Phase 1 spec.
- Online payment processing — still Venmo/Zelle, per the original system design.
- Cleaning task generation on confirmation — that's Phase 3.
- A UI for editing the contract template or rate config beyond a minimal admin endpoint — a full dashboard is Phase 6.
- Activating the 14.45% tax rate — deliberately deferred behind a flag until registration is confirmed.
