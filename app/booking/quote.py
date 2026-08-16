from dataclasses import dataclass

# Airbnb's guest service fee is roughly 14-16%; passing this back means a
# direct-booking guest pays noticeably less than booking on Airbnb, while the
# operator nets about the same as an Airbnb payout would have given them.
DIRECT_BOOKING_DISCOUNT = 0.14


@dataclass(frozen=True)
class Quote:
    nights: int
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int


def compute_quote(
    *, nightly_rates_cents: list[int], tax_enabled: bool, tax_rate_percent: float
) -> Quote:
    if not nightly_rates_cents:
        raise ValueError("at least one night is required")
    nights = len(nightly_rates_cents)
    subtotal_cents = sum(nightly_rates_cents)
    discount_cents = round(subtotal_cents * DIRECT_BOOKING_DISCOUNT)
    taxable_cents = subtotal_cents - discount_cents
    tax_cents = round(taxable_cents * tax_rate_percent / 100) if tax_enabled else 0
    return Quote(
        nights=nights,
        subtotal_cents=subtotal_cents,
        discount_cents=discount_cents,
        tax_cents=tax_cents,
        total_cents=taxable_cents + tax_cents,
    )
