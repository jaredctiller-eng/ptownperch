from datetime import date

from icalendar import Calendar, Event


def build_calendar(entries: list[tuple[str, date, date]]) -> bytes:
    """Build an all-day-busy .ics from (uid, start, end_exclusive) entries.

    Summaries are intentionally generic: Airbnb imports this feed, so it
    must never contain guest names or contact details.
    """
    cal = Calendar()
    cal.add("prodid", "-//Ptown Perch Ops//EN")
    cal.add("version", "2.0")
    for uid, start, end in entries:
        event = Event()
        event.add("uid", uid)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("summary", "Unavailable")
        cal.add_component(event)
    return cal.to_ical()
