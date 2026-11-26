from datetime import date

from app.booking.revenue import summarize_period
from app.config import settings
from app.models import DailyRate, Reservation

AUTH = ("ops", "secret")


def _res(source, status, check_in, check_out, name="G"):
    return Reservation(
        property_id=1, source=source, status=status, guest_name=name,
        check_in=check_in, check_out=check_out,
    )


def _summ(reservations, rates=None, default=10000, tax=False, start=date(2026, 10, 1),
          end=date(2026, 11, 1)):
    return summarize_period(
        reservations,
        rates_by_date=rates or {},
        default_rate_cents=default,
        tax_enabled=tax,
        tax_rate_percent=10.0,
        start=start,
        end=end,
    )


def test_confirmed_is_collected_and_hold_is_outstanding():
    s = _summ([
        _res("direct", "confirmed", date(2026, 10, 1), date(2026, 10, 3)),  # 2 nights
        _res("direct", "hold", date(2026, 10, 10), date(2026, 10, 11)),     # 1 night
    ])
    # 14% direct discount: 20000 -> 17200 ; 10000 -> 8600
    assert s.collected_cents == 17200
    assert s.outstanding_cents == 8600
    assert s.direct_nights == 3


def test_checked_out_counts_as_collected():
    s = _summ([_res("direct", "checked_out", date(2026, 10, 1), date(2026, 10, 2))])
    assert s.collected_cents == 8600


def test_inquiry_and_canceled_excluded():
    s = _summ([
        _res("direct", "inquiry", date(2026, 10, 1), date(2026, 10, 3)),
        _res("direct", "canceled", date(2026, 10, 5), date(2026, 10, 7)),
    ])
    assert s.collected_cents == 0
    assert s.outstanding_cents == 0
    assert s.occupied_nights == 0


def test_per_date_rate_overrides_default():
    s = _summ(
        [_res("direct", "confirmed", date(2026, 10, 1), date(2026, 10, 3))],
        rates={date(2026, 10, 1): 30000},
    )
    # nights: 30000 + 10000 = 40000 -> minus 14% = 34400
    assert s.collected_cents == 34400


def test_stay_is_prorated_to_period():
    s = _summ([_res("direct", "confirmed", date(2026, 9, 29), date(2026, 10, 3))])
    # only Oct 1 and Oct 2 fall inside October
    assert s.direct_nights == 2
    assert s.collected_cents == 17200


def test_tax_applied_when_enabled():
    s = _summ(
        [_res("direct", "confirmed", date(2026, 10, 1), date(2026, 10, 2))], tax=True
    )
    # 10000 - 1400 = 8600 ; tax 10% = 860
    assert s.collected_cents == 9460


def test_airbnb_counts_toward_occupancy_but_not_revenue():
    s = _summ([
        _res("airbnb_ical", "confirmed", date(2026, 10, 1), date(2026, 10, 6)),
        _res("direct", "confirmed", date(2026, 10, 10), date(2026, 10, 12)),
    ])
    assert s.airbnb_nights == 5
    assert s.direct_nights == 2
    assert s.occupied_nights == 7
    assert s.total_nights == 31
    assert round(s.occupancy_pct, 1) == round(7 / 31 * 100, 1)
    assert s.collected_cents == 17200  # direct only


def test_missing_rate_counted_as_unpriced_not_silently_zero():
    s = _summ(
        [_res("direct", "confirmed", date(2026, 10, 1), date(2026, 10, 3))],
        default=None,
        rates={date(2026, 10, 1): 10000},
    )
    assert s.unpriced_nights == 1
    assert s.collected_cents == 8600


async def test_dashboard_shows_revenue_panel(client, session, prop, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    prop.nightly_rate_cents = 10000
    session.add(DailyRate(property_id=prop.id, date=date(2026, 10, 1), rate_cents=30000))
    session.add(Reservation(
        property_id=prop.id, source="direct", status="confirmed", guest_name="Paid Guest",
        check_in=date(2026, 10, 1), check_out=date(2026, 10, 3),
    ))
    await session.flush()

    resp = await client.get("/dashboard?year=2026&month=10", auth=AUTH)
    assert resp.status_code == 200
    body = resp.text
    assert "<h2>Revenue</h2>" in body
    assert "$344.00" in body  # 30000+10000 minus 14%
    assert "direct bookings only" in body.lower()
