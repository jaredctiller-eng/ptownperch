# Handoff — direct-booking contract and the agreed-total model

**Date:** 2026-11-26
**Merged:** PR #2, "Price direct bookings from a pre-agreed flat total" (`9088694`)
**Context:** a long chat session with Jared that produced the rental agreement
text, settled every standing term, and rebuilt the pricing model around a
negotiated flat total.

Read this before touching the booking path. It records decisions that are not
derivable from the code, and the open items in the order they matter.

---

## 1. The pricing model changed shape

Direct bookings are **negotiated over text with a returning guest**. Jared and
Frank agree on one number; that number is what the guest pays, all in. The app
no longer computes the price for a direct booking — it records the agreed one
and derives everything else.

- `app/booking/terms.py` is the single home for that derivation.
- The total is entered in the **Slack approval message** (an input block, read
  from `payload["state"]["values"]` on Approve) and stored as
  `Reservation.agreed_total_cents`.
- Validation happens **before** the property lock, the overlap re-check and the
  commit, so a typo leaves the reservation untouched.

### Two pricing paths now coexist — this is the main open design question

| Path | How it prices | Status |
|---|---|---|
| Slack approval → contract | the agreed total | current, intended |
| Public `/book` inquiry page | `compute_quote`: nightly rates − 14%, tax off | untouched, legacy |

`compute_quote` was deliberately left alone in PR #2 because `/book` quotes a
guest *before* a human has negotiated anything. But the 14% discount it applies
contradicts what Jared decided this session (see §2), and the two paths can now
show a guest different numbers for the same dates.

**Unresolved:** whether `/book` should stop quoting entirely and become a pure
inquiry form, with the price always set by a human at Slack approval. Jared has
not decided. Do not change it without asking — it is a money decision.

## 2. Standing terms, as decided

These are constants at the top of `app/booking/terms.py`. Changing one changes
every future booking; already-signed contracts are unaffected because the guest
page renders `contract_snapshot`, frozen at signing time.

| Term | Value |
|---|---|
| Advance payment | 50% of the total, at booking |
| Balance | the remainder, 30 days before check-in |
| Security deposit | $500 flat, refundable, paid with the balance, on top of the total |
| Deposit returned | within 14 days of check-out |
| Cleaning fee | $200 (folded into the agreed total; no longer a separate field) |
| Cancellation | 100% less $50 before 60 days · 50% from 60–30 · none inside 30 |
| Smoking remediation | $500 |
| Undisclosed pet | $500 |
| Unauthorized overnight occupant | $150 per night |
| Overnight occupancy | 4 maximum |
| Daytime visitors | 2 at a time, 6 persons total on the property |
| Quiet hours | 10:00 PM – 8:00 AM |
| Parking | none provided; public municipal lots at the guest's expense |
| Pets | none; service animals accommodated as law requires |

Rejected along the way, with reasons, so they don't get re-proposed:

- **A damage-protection fee instead of a deposit.** Jared chose the refundable
  deposit. A refundable deposit is also **not taxable** under 830 CMR
  64G.1.1(5)(a)1, where a non-refundable fee would be.
- **A $1,000 security deposit.** Too high for a one-bedroom, and jarring against
  Airbnb, which collects no deposit from guests at all.
- **Counting daytime visitors against the 4-person cap.** Unenforceable, and an
  unenforced clause weakens the agreement. Overnight occupancy and daytime
  visitors are now separate rules.

## 3. Tax: the decision and what it costs

**The agreed total is tax-inclusive, and the excise is not itemized to the
guest.** Jared chose to keep the contract simple: one number.

This does not remove the obligation. 14.45% (5.7% state + 6.0% Provincetown
local + 2.75% Cape Cod & Islands Water Protection Fund) still comes out of every
direct booking at filing time, monthly through MassTaxConnect by the 20th for
the prior month. Airbnb remits its own bookings as an intermediary; direct
bookings are self-reported.

`rent_and_excise_cents()` in `terms.py` does that split for bookkeeping. It is
not guest-facing and nothing in the request path calls it. A monthly reporting
job or an admin view is a reasonable thing to build on it — Jared has not asked.

`Property.direct_booking_tax_enabled` / `direct_booking_tax_rate_percent` remain
in the model, **disabled**. Itemizing tax to guests later is a config change,
not another migration.

## 4. Open items, in priority order

### 4.1 Load the contract template — blocks the first direct booking

The rental agreement text exists but is **not in the database**. Until it is,
`/book/{token}` refuses to render.

The full agreement was produced in the chat session, branded to match
ptownperch.com, and is in Jared's Google Drive folder "Ptown Perch — Direct
Bookings". It needs converting to the Jinja variables below and loading via
`PUT /admin/contract-template`.

Variables `_quote_context` provides (`app/routes/booking_public.py`):

```
guest_name        guest_email        guest_phone
check_in          check_out          (long form: "July 9, 2027")
check_in_iso      check_out_iso      nights
total             advance_payment    balance
balance_due_date  security_deposit   due_with_balance
deposit_return_days
```

Two traps:

1. **`has_unresolved_blanks` rejects any `[...]` in the template** — the regex is
   `\[[^\]{}]*\]`. No square brackets anywhere, including in prose or a markdown
   link. This is checked at the admin endpoint *and* again at render time.
2. **`render_contract` uses `StrictUndefined`.** A variable name that isn't in
   the list above raises, and the guest sees a 503. Verify with
   `GET /admin/status` → `contract_has_unresolved_blanks` before sharing a link.

### 4.2 There is no CI — nothing verifies a PR

No `.github/workflows`, zero check runs, zero commit statuses. PR #2's "206
passing" was a local run and nothing more. This repo now holds money arithmetic
that lands in a signed document.

A single workflow running `pytest tests/ -q` and `ruff check app/ tests/` on
pull requests would close this. Note `app/routes/dashboard.py` carries two
pre-existing ruff findings (I001, C408) that predate PR #2 — fix them in the
same change or the first CI run is red.

### 4.3 Items that need Jared, not code

These gate the first real direct booking and no amount of code solves them:

- **Insurance.** His condo policy shows $500k liability with short-term-rental
  application *unconfirmed*, and most HO-6 policies exclude business use.
  Massachusetts requires $1M for STR operators, with an exemption only when
  renting through a platform providing equal cover — which Airbnb does and a
  direct booking does not. He has emailed his broker (BY&D) about 2027 coverage.
  **Do not encourage a first direct booking before that answer lands.**
- **MA DOR registration.** He does not know his Certificate of Registration
  number and may never have seen it; Airbnb may have registered him as an
  intermediary. Needs checking on MassTaxConnect. Required before self-filing.
- **Attorney review** of the agreement. Three clauses to flag specifically: the
  prevailing-party fee award (cuts both ways), the jury waiver (enforceable in
  MA but scrutinised in consumer settings), and the service-animal carve-out,
  which assumes ADA Title III rather than the Fair Housing Act — genuinely
  unsettled for a transiently rented condo.

### 4.4 The house manual does not exist

The agreement references it in four places: condominium rules, trash and
recycling, parking, and as part of the "entire agreement" clause. A contract
that incorporates a document nobody has written is a loose end. It is also where
the Grace Hall parking recommendation belongs — Jared was explicit that the
recommendation does not go in the contract.

## 5. Things worth knowing about how this was built

- **Slack has no bot token here.** Only `SLACK_WEBHOOK_URL` and
  `SLACK_SIGNING_SECRET`. That rules out `views.open`, so modals are unavailable
  — hence the input block in the message. If a modal is ever wanted, that needs
  a new Slack app config and a `SLACK_BOT_TOKEN`, which only Jared can create.
- **Google Docs eSignature was evaluated and rejected.** It requires Workspace
  Individual or Business; his account is free Gmail. The app's own draw-pad
  signature flow is what's in use and works.
- **Money is integer cents and `Decimal` throughout `terms.py`.** No float
  touches an amount. The advance rounds half-up and the balance takes the
  remainder so the two always sum to exactly the total. Keep it that way.
- **An LLM must never compute these amounts.** The chat session considered a
  Claude-API extraction step that would read a text thread and fill the
  contract. If that is ever built, it extracts fields only; arithmetic stays in
  `terms.py`, and a human approves the figures before a contract is generated.
