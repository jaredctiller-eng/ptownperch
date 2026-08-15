from datetime import UTC, date, datetime, timedelta

from app.booking.hold_expiry import expire_stale_holds
from app.config import settings
from app.models import Reservation


def _hold(prop, hours_old, **kwargs):
    return Reservation(
        property_id=prop.id, source="direct", status="hold",
        guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        created_at=datetime.now(UTC) - timedelta(hours=hours_old),
        **kwargs,
    )


async def test_fresh_hold_untouched(session, prop):
    res = _hold(prop, hours_old=1)
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "hold"
    assert res.hold_warned_at is None


async def test_hold_past_warning_threshold_gets_warned_once(session, prop, monkeypatch):
    sent = []

    async def fake_post_slack(text):
        sent.append(text)

    monkeypatch.setattr("app.booking.hold_expiry.post_slack", fake_post_slack)
    res = _hold(prop, hours_old=settings.hold_warning_hours + 1)
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "hold"
    assert res.hold_warned_at is not None
    assert len(sent) == 1

    # second run within the same window must not warn again
    await expire_stale_holds(session)
    assert len(sent) == 1


async def test_hold_past_expiry_threshold_released(session, prop, monkeypatch):
    async def fake_post_slack(text):
        pass

    monkeypatch.setattr("app.booking.hold_expiry.post_slack", fake_post_slack)
    res = _hold(
        prop, hours_old=settings.hold_expiry_hours + 1,
        hold_warned_at=datetime.now(UTC) - timedelta(hours=13),
    )
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "canceled"


async def test_signed_hold_still_expires_if_unpaid(session, prop, monkeypatch):
    # Signing doesn't confirm — an unpaid signed hold should still expire.
    async def fake_post_slack(text):
        pass

    monkeypatch.setattr("app.booking.hold_expiry.post_slack", fake_post_slack)
    res = _hold(
        prop, hours_old=settings.hold_expiry_hours + 1, signed_at=datetime.now(UTC),
        hold_warned_at=datetime.now(UTC) - timedelta(hours=13),
    )
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "canceled"


async def test_confirmed_reservation_never_touched(session, prop, monkeypatch):
    async def fake_post_slack(text):
        pass

    monkeypatch.setattr("app.booking.hold_expiry.post_slack", fake_post_slack)
    res = Reservation(
        property_id=prop.id, source="direct", status="confirmed",
        guest_name="Alice", check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        created_at=datetime.now(UTC) - timedelta(hours=1000),
    )
    session.add(res)
    await session.flush()

    await expire_stale_holds(session)
    await session.refresh(res)
    assert res.status == "confirmed"
