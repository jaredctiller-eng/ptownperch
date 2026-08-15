import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import require_admin
from app.config import settings
from app.db import get_session
from app.models import DIRECT_BLOCKING_STATUSES, Property, Reservation
from app.slack.notify import post_slack
from app.sync.engine import _overlaps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

CREATABLE_STATUSES = ("inquiry", "hold", "confirmed")


class ReservationCreate(BaseModel):
    check_in: date
    check_out: date
    guest_name: str
    guest_email: str | None = None
    guest_phone: str | None = None
    status: str = "confirmed"
    property_slug: str | None = None


class ReservationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    property_id: int
    source: str
    status: str
    guest_name: str | None
    guest_email: str | None
    guest_phone: str | None
    check_in: date
    check_out: date


async def _get_property_or_404(session: AsyncSession, slug: str) -> Property:
    prop = (
        await session.execute(select(Property).where(Property.slug == slug))
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(status_code=404, detail=f"unknown property: {slug}")
    return prop


async def _blocking_reservations(session: AsyncSession, property_id: int) -> list[Reservation]:
    return (
        (
            await session.execute(
                select(Reservation).where(
                    Reservation.property_id == property_id,
                    (
                        (Reservation.source == "direct")
                        & Reservation.status.in_(DIRECT_BLOCKING_STATUSES)
                    )
                    | ((Reservation.source == "airbnb_ical") & (Reservation.status != "canceled")),
                )
            )
        )
        .scalars()
        .all()
    )


@router.post("/reservations", status_code=201, response_model=ReservationOut)
async def create_reservation(
    payload: ReservationCreate, session: AsyncSession = Depends(get_session)
):
    slug = payload.property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    if payload.check_out <= payload.check_in:
        raise HTTPException(status_code=422, detail="check_out must be after check_in")
    if payload.status not in CREATABLE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {CREATABLE_STATUSES}",
        )

    if payload.status in DIRECT_BLOCKING_STATUSES:
        for existing in await _blocking_reservations(session, prop.id):
            if _overlaps(payload.check_in, payload.check_out, existing.check_in, existing.check_out):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"overlaps existing {existing.source} reservation "
                        f"#{existing.id} ({existing.check_in} → {existing.check_out})"
                    ),
                )

    reservation = Reservation(
        property_id=prop.id,
        source="direct",
        status=payload.status,
        guest_name=payload.guest_name,
        guest_email=payload.guest_email,
        guest_phone=payload.guest_phone,
        check_in=payload.check_in,
        check_out=payload.check_out,
    )
    session.add(reservation)
    await session.commit()
    await session.refresh(reservation)

    try:
        await post_slack(
            f":house: Direct booking recorded at *{prop.name}*: {reservation.guest_name} "
            f"{reservation.check_in} → {reservation.check_out} (#{reservation.id})"
        )
    except Exception:
        logger.exception("slack post failed for direct booking #%s", reservation.id)

    return reservation


@router.get("/reservations", response_model=list[ReservationOut])
async def list_reservations(
    source: str | None = None,
    status: str | None = None,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    query = select(Reservation).where(Reservation.property_id == prop.id)
    if source is not None:
        query = query.where(Reservation.source == source)
    if status is not None:
        query = query.where(Reservation.status == status)
    query = query.order_by(Reservation.check_in.desc())

    return (await session.execute(query)).scalars().all()


@router.post("/reservations/{reservation_id}/cancel", response_model=ReservationOut)
async def cancel_reservation(reservation_id: int, session: AsyncSession = Depends(get_session)):
    reservation = await session.get(Reservation, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")
    reservation.status = "canceled"
    await session.commit()
    await session.refresh(reservation)
    return reservation


@router.get("/status")
async def admin_status(
    property_slug: str | None = None, session: AsyncSession = Depends(get_session)
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    count_rows = (
        await session.execute(
            select(Reservation.source, Reservation.status, func.count())
            .where(Reservation.property_id == prop.id)
            .group_by(Reservation.source, Reservation.status)
        )
    ).all()

    span_row = (
        await session.execute(
            select(func.min(Reservation.check_in), func.max(Reservation.check_in)).where(
                Reservation.property_id == prop.id,
                Reservation.status != "canceled",
            )
        )
    ).one()

    return {
        "property": {"slug": prop.slug, "name": prop.name},
        # Boolean only: airbnb_ical_url carries a secret token and must never
        # be echoed back by an admin endpoint.
        "airbnb_ical_configured": bool(prop.airbnb_ical_url),
        "reservation_counts": [
            {"source": source, "status": status, "count": count}
            for source, status, count in count_rows
        ],
        "earliest_check_in": span_row[0],
        "latest_check_in": span_row[1],
    }
