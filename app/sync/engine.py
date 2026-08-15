from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ical.parse import IcalEvent
from app.models import Reservation


@dataclass(frozen=True)
class Collision:
    airbnb_uid: str
    direct_reservation_id: int


@dataclass
class SyncResult:
    new_events: list[IcalEvent] = field(default_factory=list)
    changed_events: list[IcalEvent] = field(default_factory=list)
    canceled_uids: list[str] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.new_events or self.changed_events or self.canceled_uids or self.collisions)


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    # End dates are exclusive, so back-to-back stays don't overlap.
    return a_start < b_end and b_start < a_end


async def apply_airbnb_events(
    session: AsyncSession, property_id: int, events: list[IcalEvent]
) -> SyncResult:
    result = SyncResult()

    existing = (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    Reservation.source == "airbnb_ical",
                )
            )
        )
        .scalars()
        .all()
    )
    by_uid = {r.external_uid: r for r in existing}
    feed_uids = {e.uid for e in events}

    for event in events:
        if event.uid in by_uid:
            reservation = by_uid[event.uid]
            if reservation.status == "canceled":
                # Resurrected: the feed dropped it (transient glitch or a real
                # cancel/rebook) and it's back. Reactivate rather than trying
                # to insert a new row, which would violate the UID uniqueness
                # constraint.
                reservation.status = "confirmed"
                reservation.check_in = event.start
                reservation.check_out = event.end
                result.new_events.append(event)
            elif (reservation.check_in, reservation.check_out) != (event.start, event.end):
                reservation.check_in = event.start
                reservation.check_out = event.end
                result.changed_events.append(event)
        else:
            session.add(
                Reservation(
                    property_id=property_id,
                    source="airbnb_ical",
                    status="confirmed",
                    external_uid=event.uid,
                    check_in=event.start,
                    check_out=event.end,
                )
            )
            result.new_events.append(event)

    for uid, reservation in by_uid.items():
        if uid not in feed_uids and reservation.status != "canceled":
            reservation.status = "canceled"
            result.canceled_uids.append(uid)

    direct = (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    Reservation.source == "direct",
                    Reservation.status.in_(("hold", "confirmed")),
                )
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        for d in direct:
            if _overlaps(event.start, event.end, d.check_in, d.check_out):
                result.collisions.append(Collision(event.uid, d.id))

    await session.flush()
    return result
