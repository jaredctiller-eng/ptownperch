from datetime import date

from sqlalchemy import select

from app.ical.parse import IcalEvent
from app.models import Reservation
from app.sync.engine import apply_airbnb_events


def ev(uid="a@airbnb.com", start=date(2026, 9, 1), end=date(2026, 9, 5), summary="Reserved"):
    return IcalEvent(uid=uid, start=start, end=end, summary=summary)


async def test_new_event_inserted(session, prop):
    result = await apply_airbnb_events(session, prop.id, [ev()])
    assert [e.uid for e in result.new_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.source == "airbnb_ical"
    assert stored.status == "confirmed"
    assert stored.check_in == date(2026, 9, 1)


async def test_second_run_is_idempotent(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    result = await apply_airbnb_events(session, prop.id, [ev()])
    assert result.has_changes is False


async def test_missing_uid_marks_canceled(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    result = await apply_airbnb_events(session, prop.id, [])
    assert result.canceled_uids == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert stored.status == "canceled"


async def test_changed_dates_updated(session, prop):
    await apply_airbnb_events(session, prop.id, [ev()])
    moved = ev(start=date(2026, 9, 2), end=date(2026, 9, 6))
    result = await apply_airbnb_events(session, prop.id, [moved])
    assert [e.uid for e in result.changed_events] == ["a@airbnb.com"]
    stored = (await session.execute(select(Reservation))).scalar_one()
    assert (stored.check_in, stored.check_out) == (date(2026, 9, 2), date(2026, 9, 6))


async def test_collision_with_direct_reservation(session, prop):
    direct = Reservation(
        property_id=prop.id, source="direct", status="hold",
        check_in=date(2026, 9, 3), check_out=date(2026, 9, 7),
    )
    session.add(direct)
    await session.flush()
    result = await apply_airbnb_events(session, prop.id, [ev()])  # 9/1-9/5 overlaps 9/3-9/7
    assert len(result.collisions) == 1
    assert result.collisions[0].airbnb_uid == "a@airbnb.com"
    assert result.collisions[0].direct_reservation_id == direct.id


async def test_adjacent_dates_do_not_collide(session, prop):
    # checkout day == next check-in day is fine (end date is exclusive)
    direct = Reservation(
        property_id=prop.id, source="direct", status="confirmed",
        check_in=date(2026, 9, 5), check_out=date(2026, 9, 8),
    )
    session.add(direct)
    await session.flush()
    result = await apply_airbnb_events(session, prop.id, [ev()])  # ends 9/5
    assert result.collisions == []
