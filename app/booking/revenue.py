from dataclasses import dataclass
from datetime import date, timedelta

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
    airbnb_collected_cents: int = 0

    @property
    def occupancy_pct(self) -> float:
        return (self.occupied_nights / self.total_nights * 100) if self.total_nights else 0.0


def _nights_in_period(r: Reservation, start: date, end: date) -> list[date]:
    lo, hi = max(r.check_in, start), min(r.check_out, end)
    return [lo + timedelta(days=i) for i in range(max((hi - lo).days, 0))]


def summarize_period(
    reservations: list[Reservation], *, start: date, end: date
) -> PeriodSummary:
    """Revenue and occupancy for nights in [start, end).

    A direct booking's price is the flat total agreed with the guest
    (Reservation.agreed_total_cents, tax-inclusive, cleaning fee folded in;
    the refundable security deposit is not revenue and is excluded). A stay
    that straddles the period boundary contributes its total prorated by
    nights, rounded half-up. Airbnb (iCal) bookings carry no price; the payout
    is entered by hand (airbnb_payout_cents) and counts as collected when set. A direct stay with no agreed total is counted in
    ``unpriced_nights`` rather than silently treated as zero.
    """
    collected = outstanding = unpriced = airbnb_rev = 0
    direct_nights = airbnb_nights = 0
    occupied: set[date] = set()

    for r in reservations:
        if r.status not in _OCCUPYING or r.is_airbnb_block:
            continue  # Airbnb "Not available" blocks aren't stays
        nights = _nights_in_period(r, start, end)
        if not nights:
            continue
        occupied.update(nights)

        if r.source == "airbnb_ical":
            airbnb_nights += len(nights)
            if r.airbnb_payout_cents is not None:
                stay_nights = (r.check_out - r.check_in).days
                airbnb_rev += (r.airbnb_payout_cents * len(nights) * 2 + stay_nights) // (
                    stay_nights * 2
                )
            continue
        if r.source != "direct":
            continue

        direct_nights += len(nights)
        if r.agreed_total_cents is None:
            unpriced += len(nights)
            continue
        stay_nights = (r.check_out - r.check_in).days
        share = (r.agreed_total_cents * len(nights) * 2 + stay_nights) // (stay_nights * 2)
        if r.status in _COLLECTED:
            collected += share
        else:
            outstanding += share

    return PeriodSummary(
        collected_cents=collected + airbnb_rev,
        airbnb_collected_cents=airbnb_rev,
        outstanding_cents=outstanding,
        direct_nights=direct_nights,
        airbnb_nights=airbnb_nights,
        occupied_nights=len(occupied),
        total_nights=(end - start).days,
        unpriced_nights=unpriced,
    )
