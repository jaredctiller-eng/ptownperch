from datetime import date

from app.ical.parse import parse_airbnb_ical

AIRBNB_ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Airbnb Inc//Hosting Calendar 1.0//EN\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20260901\r
DTEND;VALUE=DATE:20260905\r
UID:1418fb9d5d4d-8b47c0a@airbnb.com\r
SUMMARY:Reserved\r
DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/de\r
 tails/HMABCDE\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20261010\r
DTEND;VALUE=DATE:20261012\r
UID:5f2b1c-blocked@airbnb.com\r
SUMMARY:Airbnb (Not available)\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_parses_events_sorted_by_start():
    events = parse_airbnb_ical(AIRBNB_ICS)
    assert len(events) == 2
    first, second = events
    assert first.uid == "1418fb9d5d4d-8b47c0a@airbnb.com"
    assert first.start == date(2026, 9, 1)
    assert first.end == date(2026, 9, 5)  # exclusive checkout day
    assert first.summary == "Reserved"
    assert first.is_reservation is True
    assert second.uid == "5f2b1c-blocked@airbnb.com"
    assert second.start == date(2026, 10, 10)
    assert second.end == date(2026, 10, 12)
    assert second.summary == "Airbnb (Not available)"
    assert second.is_reservation is False


def test_empty_calendar():
    empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nEND:VCALENDAR\r\n"
    assert parse_airbnb_ical(empty) == []
