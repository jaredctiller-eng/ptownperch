from dataclasses import dataclass
from datetime import date, datetime

from icalendar import Calendar


@dataclass(frozen=True)
class IcalEvent:
    uid: str
    start: date  # check-in
    end: date  # check-out (exclusive, per iCal DTEND semantics)
    summary: str

    @property
    def is_reservation(self) -> bool:
        # Airbnb uses SUMMARY "Reserved" for bookings and
        # "Airbnb (Not available)" for host/system blocks.
        return self.summary.lower().startswith("reserved")


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def parse_airbnb_ical(text: str) -> list[IcalEvent]:
    cal = Calendar.from_ical(text)
    events = [
        IcalEvent(
            uid=str(component["UID"]),
            start=_as_date(component.decoded("DTSTART")),
            end=_as_date(component.decoded("DTEND")),
            summary=str(component.get("SUMMARY", "")),
        )
        for component in cal.walk("VEVENT")
    ]
    return sorted(events, key=lambda e: (e.start, e.uid))
