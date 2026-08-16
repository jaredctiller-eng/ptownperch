from datetime import date

from app.booking.calendar_view import build_month_calendar
from app.models import CalendarBlock, Reservation


def _res(**overrides):
    base = {
        "id": 1,
        "property_id": 1,
        "source": "direct",
        "status": "hold",
        "guest_name": "Guest",
        "check_in": date(2026, 10, 1),
        "check_out": date(2026, 10, 2),
    }
    base.update(overrides)
    return Reservation(**base)


def _block(**overrides):
    base = {
        "id": 1,
        "property_id": 1,
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 2),
        "reason": "maintenance",
    }
    base.update(overrides)
    return CalendarBlock(**base)


def test_empty_month_all_available():
    days = build_month_calendar(2026, 10, [], [])
    assert len(days) == 31
    assert all(d.status == "available" and d.label is None for d in days)
    assert days[0].date == date(2026, 10, 1)
    assert days[-1].date == date(2026, 10, 31)


def test_direct_hold_spans_three_days():
    res = _res(
        source="direct", status="hold", guest_name="Jane Doe",
        check_in=date(2026, 10, 10), check_out=date(2026, 10, 13),
    )
    days = build_month_calendar(2026, 10, [res], [])
    by_date = {d.date: d for d in days}
    for day_num in (10, 11, 12):
        d = by_date[date(2026, 10, day_num)]
        assert d.status == "direct_hold"
        assert d.label == "Jane Doe"
    # days outside the range unaffected
    assert by_date[date(2026, 10, 9)].status == "available"
    assert by_date[date(2026, 10, 13)].status == "available"


def test_airbnb_confirmed_shows_no_label():
    res = _res(
        source="airbnb_ical", status="confirmed", guest_name=None,
        check_in=date(2026, 10, 5), check_out=date(2026, 10, 7),
    )
    days = build_month_calendar(2026, 10, [res], [])
    by_date = {d.date: d for d in days}
    for day_num in (5, 6):
        d = by_date[date(2026, 10, day_num)]
        assert d.status == "airbnb"
        assert d.label is None  # never leak guest info for airbnb rows


def test_calendar_block_marks_blocked_when_no_reservation():
    block = _block(start_date=date(2026, 10, 20), end_date=date(2026, 10, 22))
    days = build_month_calendar(2026, 10, [], [block])
    by_date = {d.date: d for d in days}
    assert by_date[date(2026, 10, 20)].status == "blocked"
    assert by_date[date(2026, 10, 21)].status == "blocked"
    assert by_date[date(2026, 10, 22)].status == "available"


def test_checkout_day_is_not_departing_reservation_status():
    res = _res(
        source="direct", status="confirmed", guest_name="Sam",
        check_in=date(2026, 10, 15), check_out=date(2026, 10, 18),
    )
    days = build_month_calendar(2026, 10, [res], [])
    by_date = {d.date: d for d in days}
    assert by_date[date(2026, 10, 17)].status == "direct_confirmed"
    # checkout day itself (exclusive end) must not show the departing status
    assert by_date[date(2026, 10, 18)].status == "available"


def test_inquiry_and_canceled_reservations_ignored():
    inquiry = _res(
        source="direct", status="inquiry", guest_name="Ignored Inquiry",
        check_in=date(2026, 10, 3), check_out=date(2026, 10, 5),
    )
    canceled = _res(
        source="direct", status="canceled", guest_name="Ignored Canceled",
        check_in=date(2026, 10, 25), check_out=date(2026, 10, 27),
    )
    days = build_month_calendar(2026, 10, [inquiry, canceled], [])
    by_date = {d.date: d for d in days}
    for day_num in (3, 4, 25, 26):
        d = by_date[date(2026, 10, day_num)]
        assert d.status == "available"
        assert d.label is None
