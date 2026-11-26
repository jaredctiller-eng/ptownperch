from dataclasses import dataclass
from datetime import date, timedelta

from app.booking.quote import compute_quote
from app.models import Reservation

# Reservations that occupy the property. Inquiries and cancellations don't.
_OCCUPYING = ("hold", "confirmed", "checked_out")
# A direct booking is "collected" once marked paid (hold -> confirmed).
_COLLECTED = ("confirmed", "checked_out")


@dataclass(frozen=True)
class PeriodSummary:
    collected_cents: int
    outstanding_cents: int
    direct_nights: int
    airbnb_nights: int
    occupied_nights: int
    total_nights: int
    unpriced_nights: int

    @property
    def occupancy_pct(self) -> float:
        return (self.occupied_nights / self.total_nights * 100) if self.total_nights else 0.0


def _nights_in_period(r: Reservation, start: date, end: date) -> list[date]:
    lo, hi = max(r.check_in, start), min(r.check_out, end)
    return [lo + timedelta(days=i) for i in range(max((hi - lo).days, 0))]


def summarize_period(
    reservations: list[Reservation],
    *,
    rates_by_date: dict[date, int],
    default_rate_cents: int | None,
    tax_enabled: bool,
    tax_rate_percent: float,
    start: date,
    end: date,
) -> PeriodSummary:
    """Estimate revenue and occupancy for nights in [start, end).

    Reservations don't store a price, so direct revenue is recomputed from the
    *current* nightly rates through the quote engine; it can drift if rates
    were edited after a booking. Airbnb (iCal) bookings carry no price and
    count toward occupancy only.
    """
    collected = outstanding = unpriced = 0
    direct_nights = airbnb_nights = 0
    occupied: set[date] = set()

    for r in reservations:
        if r.status not in _OCCUPYING:
            continue
        nights = _nights_in_period(r, start, end)
        if not nights:
            continue
        occupied.update(nights)

        if r.source == "airbnb_ical":
            airbnb_nights += len(nights)
            continue
        if r.source != "direct":
            continue

        direct_nights += len(nights)
        rates = []
        for n in nights:
            rate = rates_by_date.get(n, default_rate_cents)
            if rate is None:
                unpriced += 1
            else:
                rates.append(rate)
        if not rates:
            continue
        total = compute_quote(
            nightly_rates_cents=rates, tax_enabled=tax_enabled, tax_rate_percent=tax_rate_percent
        ).total_cents
        if r.status in _COLLECTED:
            collected += total
        else:
            outstanding += total

    return PeriodSummary(
        collected_cents=collected,
        outstanding_cents=outstanding,
        direct_nights=direct_nights,
        airbnb_nights=airbnb_nights,
        occupied_nights=len(occupied),
        total_nights=(end - start).days,
        unpriced_nights=unpriced,
    )
