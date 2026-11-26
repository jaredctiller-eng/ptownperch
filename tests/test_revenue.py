from datetime import date

from app.booking.revenue import summarize_period
from app.config import settings
from app.models import Reservation

AUTH = ("ops", "secret")


def _res(source, status, check_in, check_out, name="G", total=None):
    return Reservation(
        property_id=1, source=source, status=status, guest_name=name,
        check_in=check_in, check_out=check_out, agreed_total_cents=total,
    )


def _summ(reservations, start=date(2026, 10, 1), end=date(2026, 11, 1)):
    return summarize_period(reservations, start=start, end=end)


def test_confirmed_is_collected_and_hold_is_outstanding():
    s = _summ([
        _res("direct", "confirmed", date(2026, 10, 1), date(2026, 10, 3), total=120000),
        _res("direct", "hold", date(2026, 10, 10), date(2026, 10, 11), total=60000),
    ])
    assert s.collected_cents == 120000
    assert s.outstanding_cents == 60000
    assert s.direct_nights == 3


def test_checked_out_counts_as_collected():
    s = _summ([_res("direct", "checked_out", date(2026, 10, 1), date(2026, 10, 2), total=50000)])
    assert s.collected_cents == 50000


def test_inquiry_and_canceled_excluded():
    s = _summ([
        _res("direct", "inquiry", date(2026, 10, 1), date(2026, 10, 3), total=1),
        _res("direct", "canceled", date(2026, 10, 5), date(2026, 10, 7), total=1),
    ])
    assert s.collected_cents == 0
    assert s.outstanding_cents == 0
    assert s.occupied_nights == 0


def test_stay_is_prorated_to_period():
    # 4-night stay, 2 nights (Oct 1, 2) inside October -> half of the total
    s = _summ([_res("direct", "confirmed", date(2026, 9, 29), date(2026, 10, 3), total=100001)])
    assert s.direct_nights == 2
    assert s.collected_cents == 50001  # 50000.5 rounds half-up


def test_prorated_halves_across_months_sum_to_total():
    r = _res("direct", "confirmed", date(2026, 9, 29), date(2026, 10, 3), total=100001)
    sep = _summ([r], start=date(2026, 9, 1), end=date(2026, 10, 1)).collected_cents
    octo = _summ([r]).collected_cents
    assert sep + octo in (100001, 100002)  # at most 1 cent of rounding


def test_airbnb_counts_toward_occupancy_but_not_revenue():
    s = _summ([
        _res("airbnb_ical", "confirmed", date(2026, 10, 1), date(2026, 10, 6)),
        _res("direct", "confirmed", date(2026, 10, 10), date(2026, 10, 12), total=90000),
    ])
    assert s.airbnb_nights == 5
    assert s.direct_nights == 2
    assert s.occupied_nights == 7
    assert s.total_nights == 31
    assert round(s.occupancy_pct, 1) == round(7 / 31 * 100, 1)
    assert s.collected_cents == 90000  # direct only


def test_missing_agreed_total_counted_as_unpriced_not_silently_zero():
    s = _summ([_res("direct", "hold", date(2026, 10, 1), date(2026, 10, 3))])
    assert s.unpriced_nights == 2
    assert s.outstanding_cents == 0
    assert s.occupied_nights == 2


async def test_dashboard_shows_revenue_panel(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    session.add(Reservation(
        property_id=prop.id, source="direct", status="confirmed", guest_name="Paid Guest",
        check_in=date(2026, 10, 1), check_out=date(2026, 10, 3), agreed_total_cents=123400,
    ))
    await session.flush()

    resp = await client.get("/dashboard?year=2026&month=10", auth=AUTH)
    assert resp.status_code == 200
    body = resp.text
    assert "<h2>Revenue</h2>" in body
    assert "$1,234.00" in body
    assert "direct bookings only" in body.lower()
