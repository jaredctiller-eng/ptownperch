from datetime import date

from app.ical.generate import build_calendar
from app.ical.parse import parse_airbnb_ical


def test_roundtrips_through_parser():
    ics = build_calendar(
        [
            ("res-1@ptownperch", date(2026, 9, 1), date(2026, 9, 5)),
            ("block-2@ptownperch", date(2026, 10, 1), date(2026, 10, 3)),
        ]
    )
    events = parse_airbnb_ical(ics.decode())
    assert [e.uid for e in events] == ["res-1@ptownperch", "block-2@ptownperch"]
    assert events[0].start == date(2026, 9, 1)
    assert events[0].end == date(2026, 9, 5)


def test_no_guest_details_leak():
    # This feed is imported by Airbnb — summaries must be generic.
    ics = build_calendar([("res-1@ptownperch", date(2026, 9, 1), date(2026, 9, 5))])
    assert b"SUMMARY:Unavailable" in ics


def test_empty_calendar_is_valid():
    events = parse_airbnb_ical(build_calendar([]).decode())
    assert events == []
