import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import require_admin
from app.booking.availability import assert_no_overlap
from app.booking.contract import has_unresolved_blanks
from app.booking.mark_paid import MarkPaidError
from app.booking.mark_paid import mark_paid as apply_mark_paid
from app.config import settings
from app.db import get_session
from app.models import DIRECT_BLOCKING_STATUSES, Property, Reservation
from app.slack.notify import post_slack

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

CREATABLE_STATUSES = ("inquiry", "hold", "confirmed")


class ReservationCreate(BaseModel):
    check_in: date
    check_out: date
    guest_name: str = Field(max_length=128)
    guest_email: str | None = Field(default=None, max_length=255)
    guest_phone: str | None = Field(default=None, max_length=32)
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


async def _get_property_or_404(
    session: AsyncSession, slug: str, *, for_update: bool = False
) -> Property:
    query = select(Property).where(Property.slug == slug)
    if for_update:
        # Row lock so two concurrent creates for the same property serialize
        # around the overlap check below instead of both reading a
        # pre-insert state and both passing (TOCTOU). Only takes effect on
        # Postgres; SQLite ignores FOR UPDATE.
        query = query.with_for_update()
    prop = (await session.execute(query)).scalar_one_or_none()
    if prop is None:
        # Don't echo the caller-supplied slug back into the error body.
        raise HTTPException(status_code=404, detail="unknown property")
    return prop


@router.post("/reservations", status_code=201, response_model=ReservationOut)
async def create_reservation(
    payload: ReservationCreate, session: AsyncSession = Depends(get_session)
):
    slug = payload.property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug, for_update=True)

    if payload.check_out <= payload.check_in:
        raise HTTPException(status_code=422, detail="check_out must be after check_in")
    if payload.status not in CREATABLE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {CREATABLE_STATUSES}",
        )

    if payload.status in DIRECT_BLOCKING_STATUSES:
        await assert_no_overlap(session, prop.id, payload.check_in, payload.check_out)

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
async def cancel_reservation(
    reservation_id: int,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    # Scoped to source="direct" and this property: an airbnb_ical-sourced row
    # must never be cancellable here. Canceling one would free its dates on
    # the outbound feed, and the next inbound sync's resurrection logic
    # (app.sync.engine.apply_airbnb_events) would flip it back to confirmed
    # as soon as Airbnb's feed still lists it — a guaranteed double booking
    # in the gap between those two events. 404 rather than 403 so a caller
    # guessing ids can't distinguish "doesn't exist" from "exists but is an
    # airbnb-sourced/other-property row".
    reservation = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id,
                Reservation.property_id == prop.id,
                Reservation.source == "direct",
            )
        )
    ).scalar_one_or_none()
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


class ConfigUpdate(BaseModel):
    nightly_rate_cents: int | None = None
    direct_booking_tax_enabled: bool | None = None
    direct_booking_tax_rate_percent: float | None = None


class ConfigOut(BaseModel):
    model_config = {"from_attributes": True}

    nightly_rate_cents: int | None
    direct_booking_tax_enabled: bool
    direct_booking_tax_rate_percent: float


@router.patch("/config", response_model=ConfigOut)
async def update_config(
    payload: ConfigUpdate,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    if payload.nightly_rate_cents is not None:
        prop.nightly_rate_cents = payload.nightly_rate_cents
    if payload.direct_booking_tax_enabled is not None:
        prop.direct_booking_tax_enabled = payload.direct_booking_tax_enabled
    if payload.direct_booking_tax_rate_percent is not None:
        prop.direct_booking_tax_rate_percent = payload.direct_booking_tax_rate_percent

    await session.commit()
    await session.refresh(prop)
    return prop


class ContractTemplateUpdate(BaseModel):
    template: str


@router.put("/contract-template")
async def update_contract_template(
    payload: ContractTemplateUpdate,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)
    prop.contract_template = payload.template
    await session.commit()
    return {"has_unresolved_blanks": has_unresolved_blanks(payload.template)}


@router.post("/reservations/{reservation_id}/mark-paid", response_model=ReservationOut)
async def mark_paid_endpoint(
    reservation_id: int,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    reservation = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id,
                Reservation.property_id == prop.id,
                Reservation.source == "direct",
            )
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")

    try:
        await apply_mark_paid(session, reservation)
    except MarkPaidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await session.refresh(reservation)

    try:
        await post_slack(
            f":moneybag: Marked paid & confirmed at *{prop.name}*: {reservation.guest_name} "
            f"{reservation.check_in} → {reservation.check_out} (#{reservation.id})"
        )
    except Exception:
        logger.exception("slack post failed for mark-paid #%s", reservation.id)

    return reservation
