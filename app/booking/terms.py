"""Payment terms derived from a pre-agreed flat total.

Direct bookings are negotiated over text: the operator and a returning guest
agree on one number, and that number is what the guest pays. Everything in the
contract's payment schedule is derived from it here, in one place, so the
Slack approval, the guest-facing page and the signed contract can never
disagree about what is owed or when.

The agreed total is tax-inclusive. The room occupancy excise is not itemized
to the guest and is not computed here; it is backed out at filing time. See
`RENT_DIVISOR` below for the figure the monthly MA DOR return needs.

Money is integer cents throughout. No float ever touches an amount.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

# Operator-set standing terms. Changing one here changes every future
# booking; already-signed contracts are unaffected because the guest page
# renders `contract_snapshot`, frozen at signing time.
ADVANCE_PERCENT = Decimal("0.50")       # due at booking, credited toward the total
BALANCE_LEAD_DAYS = 30                  # balance due this many days before check-in
SECURITY_DEPOSIT_CENTS = 50_00          # refundable, on top of the total, not rent
DEPOSIT_RETURN_DAYS = 14                # returned within this many days of check-out

# The agreed total is tax-inclusive: total / 1.1445 is the rent to report on
# the monthly MA DOR room occupancy return (5.7% state + 6.0% Provincetown
# local + 2.75% Cape Cod & Islands Water Protection Fund = 14.45%). Nothing in
# the guest-facing path uses this; it exists so the number has one home and
# `rent_and_excise_cents` below can produce it for bookkeeping.
RENT_DIVISOR = Decimal("1.1445")

MAX_TOTAL_CENTS = 100_000_00  # sanity ceiling: a typo, not a booking


class TermsError(ValueError):
    """The agreed total is not a usable amount."""


@dataclass(frozen=True)
class Terms:
    total_cents: int
    advance_cents: int
    balance_cents: int
    balance_due: date
    security_deposit_cents: int
    deposit_return_days: int

    @property
    def due_at_booking_cents(self) -> int:
        return self.advance_cents

    @property
    def due_with_balance_cents(self) -> int:
        """What the second payment actually is: balance plus the deposit."""
        return self.balance_cents + self.security_deposit_cents


def _round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_total_to_cents(raw: str) -> int:
    """Parse an operator-typed total ("2000", "$2,000", "2000.00") to cents.

    Deliberately strict: this number goes straight into a contract a guest
    signs, so anything ambiguous is rejected rather than guessed at.
    """
    if raw is None:
        raise TermsError("no total provided")
    cleaned = raw.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        raise TermsError("no total provided")
    try:
        amount = Decimal(cleaned)
    except (ArithmeticError, ValueError):
        raise TermsError(f"{raw!r} is not a number") from None
    if amount != amount.quantize(Decimal("0.01")):
        raise TermsError("more precision than cents")
    cents = _round_cents(amount * 100)
    if cents <= 0:
        raise TermsError("total must be greater than zero")
    if cents > MAX_TOTAL_CENTS:
        raise TermsError(f"{raw!r} looks like a typo (over ${MAX_TOTAL_CENTS // 100:,})")
    return cents


def compute_terms(*, total_cents: int, check_in: date) -> Terms:
    """Derive the payment schedule from the agreed total.

    The advance rounds half-up and the balance is the remainder, so the two
    always sum to exactly the total — never a cent over or under, whatever
    the rounding.
    """
    if total_cents <= 0:
        raise TermsError("total must be greater than zero")

    advance = _round_cents(Decimal(total_cents) * ADVANCE_PERCENT)
    balance = total_cents - advance
    return Terms(
        total_cents=total_cents,
        advance_cents=advance,
        balance_cents=balance,
        balance_due=check_in - timedelta(days=BALANCE_LEAD_DAYS),
        security_deposit_cents=SECURITY_DEPOSIT_CENTS,
        deposit_return_days=DEPOSIT_RETURN_DAYS,
    )


def rent_and_excise_cents(total_cents: int) -> tuple[int, int]:
    """Split a tax-inclusive total into (rent, excise) for the MA DOR return.

    Not guest-facing. The rent figure is what gets reported; the excise is
    what gets remitted. Rounds so the two always sum back to the total.
    """
    rent = _round_cents(Decimal(total_cents) / RENT_DIVISOR)
    return rent, total_cents - rent
