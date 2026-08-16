from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import require_admin
from app.booking.calendar_view import build_month_calendar
from app.booking.contract import has_unresolved_blanks
from app.config import settings
from app.db import get_session
from app.models import CalendarBlock, Reservation
from app.routes import admin
from app.routes.admin import _get_property_or_404

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")


def _today() -> date:
    return datetime.now(UTC).date()


def _prev_next_month(year: int, month: int) -> tuple[tuple[int, int], tuple[int, int]]:
    prev = (year - 1, 12) if month == 1 else (year, month - 1)
    nxt = (year + 1, 1) if month == 12 else (year, month + 1)
    return prev, nxt


def _redirect_to_dashboard(
    year: int | None, month: int | None, property_slug: str | None = None
) -> RedirectResponse:
    params = []
    if year is not None:
        params.append(f"year={year}")
    if month is not None:
        params.append(f"month={month}")
    if property_slug is not None:
        params.append(f"property_slug={property_slug}")
    url = "/dashboard"
    if params:
        url += "?" + "&".join(params)
    return RedirectResponse(url=url, status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    slug = property_slug or settings.property_slug
    prop = await _get_property_or_404(session, slug)

    today = _today()
    view_year = year or today.year
    view_month = month or today.month

    reservations = (
        await session.execute(
            select(Reservation)
            .where(Reservation.property_id == prop.id, Reservation.status != "canceled")
            .order_by(Reservation.check_in.desc())
        )
    ).scalars().all()

    blocks = (
        await session.execute(select(CalendarBlock).where(CalendarBlock.property_id == prop.id))
    ).scalars().all()

    calendar_days = build_month_calendar(view_year, view_month, list(reservations), list(blocks))
    (prev_year, prev_month), (next_year, next_month) = _prev_next_month(view_year, view_month)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "property": prop,
            "calendar_days": calendar_days,
            "reservations": reservations[:30],
            "year": view_year,
            "month": view_month,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "has_unresolved_blanks": has_unresolved_blanks(prop.contract_template or ""),
            "property_slug": property_slug,
        },
    )


@router.post("/config")
async def dashboard_update_config(
    nightly_rate_dollars: str = Form(...),
    direct_booking_tax_enabled: str = Form(""),
    year: int | None = Form(None),
    month: int | None = Form(None),
    property_slug: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    cents = round(float(nightly_rate_dollars) * 100)
    payload = admin.ConfigUpdate(
        nightly_rate_cents=cents,
        direct_booking_tax_enabled=bool(direct_booking_tax_enabled),
    )
    await admin.update_config(payload, property_slug, session)
    return _redirect_to_dashboard(year, month, property_slug)


@router.post("/contract-template")
async def dashboard_update_template(
    template: str = Form(...),
    year: int | None = Form(None),
    month: int | None = Form(None),
    property_slug: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    payload = admin.ContractTemplateUpdate(template=template)
    await admin.update_contract_template(payload, property_slug, session)
    return _redirect_to_dashboard(year, month, property_slug)


@router.post("/rates")
async def dashboard_update_rates(
    rates_text: str = Form(...),
    year: int | None = Form(None),
    month: int | None = Form(None),
    property_slug: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    parsed: dict[str, int] = {}
    for raw_line in rates_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != 2:
            raise HTTPException(status_code=422, detail=f"malformed line: {line!r}")
        date_str, dollars_str = (p.strip() for p in parts)
        try:
            cents = round(float(dollars_str) * 100)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"malformed line: {line!r}") from None
        parsed[date_str] = cents

    payload = admin.BulkRatesUpdate(rates=parsed)
    await admin.update_rates(payload, property_slug, session)
    return _redirect_to_dashboard(year, month, property_slug)
