from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from app.models import CalendarBlock, Reservation


@dataclass(frozen=True)
class CalendarDay:
    date: date
    status: str  # "available" | "direct_hold" | "direct_confirmed" | "airbnb" | "blocked"
    label: str | None  # guest name for direct bookings, None otherwise


def build_month_calendar(
    year: int, month: int, reservations: list[Reservation], blocks: list[CalendarBlock]
) -> list[CalendarDay]:
    _, days_in_month = monthrange(year, month)
    days = []
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        status = "available"
        label = None
        for r in reservations:
            if r.check_in <= d < r.check_out:
                if r.source == "airbnb_ical" and r.status == "confirmed":
                    status, label = ("blocked", None) if r.is_airbnb_block else ("airbnb", None)
                elif r.source == "direct" and r.status == "hold":
                    status, label = "direct_hold", r.guest_name
                elif r.source == "direct" and r.status == "confirmed":
                    status, label = "direct_confirmed", r.guest_name
                break  # first matching reservation wins; overlaps shouldn't exist in practice
        if status == "available":
            for b in blocks:
                if b.start_date <= d < b.end_date:
                    status = "blocked"
                    break
        days.append(CalendarDay(date=d, status=status, label=label))
    return days
