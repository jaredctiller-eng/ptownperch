from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import require_admin
from app.booking.calendar_view import build_month_calendar
from app.booking.contract import has_unresolved_blanks, render_contract
from app.booking.revenue import summarize_period
from app.booking.terms import compute_terms

# The canonical agreement, versioned in the repo so an edit is a PR rather
# than a paste into a textarea. Copying HTML out of a browser silently
# strips its tags, which is exactly how a plain-text contract once ended up
# in the database; loading from here removes that whole class of mistake.
STANDARD_AGREEMENT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "contract-template.html"
)
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
    view_year = year if year is not None else today.year
    view_month = month if month is not None else today.month

    if not (1900 <= view_year <= 2200) or not (1 <= view_month <= 12):
        raise HTTPException(status_code=422, detail="invalid year/month")

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

    month_start = date(view_year, view_month, 1)
    month_end = date(view_year + (view_month == 12), view_month % 12 + 1, 1)
    year_start = date(view_year, 1, 1)
    month_summary = summarize_period(list(reservations), start=month_start, end=month_end)
    ytd_summary = summarize_period(list(reservations), start=year_start, end=month_end)

    (prev_year, prev_month), (next_year, next_month) = _prev_next_month(view_year, view_month)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "property": prop,
            "calendar_days": calendar_days,
            "today": today,
            "month_name": month_start.strftime("%B"),
            "month_summary": month_summary,
            "ytd_summary": ytd_summary,
            "reservations": reservations,
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
    try:
        cents = round(float(nightly_rate_dollars) * 100)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"invalid rate: {nightly_rate_dollars!r}"
        ) from None
    payload = admin.ConfigUpdate(
        nightly_rate_cents=cents,
        direct_booking_tax_enabled=bool(direct_booking_tax_enabled),
    )
    await admin.update_config(payload, property_slug, session)
    return _redirect_to_dashboard(year, month, property_slug)


@router.post("/airbnb-payout")
async def dashboard_set_airbnb_payout(
    reservation_id: int = Form(...),
    payout_dollars: str = Form(""),
    year: int | None = Form(None),
    month: int | None = Form(None),
    property_slug: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    prop = await _get_property_or_404(session, property_slug or settings.property_slug)
    reservation = await session.get(Reservation, reservation_id)
    if (
        reservation is None
        or reservation.property_id != prop.id
        or reservation.source != "airbnb_ical"
        or reservation.is_airbnb_block
    ):
        raise HTTPException(status_code=404, detail="Airbnb booking not found")
    text = payout_dollars.strip().replace("$", "").replace(",", "")
    if text == "":
        reservation.airbnb_payout_cents = None
    else:
        try:
            dollars = Decimal(text)
        except InvalidOperation:
            raise HTTPException(
                status_code=422, detail=f"invalid amount: {payout_dollars!r}"
            ) from None
        if not dollars.is_finite() or dollars < 0 or dollars > 1_000_000:
            raise HTTPException(status_code=422, detail=f"invalid amount: {payout_dollars!r}")
        reservation.airbnb_payout_cents = int((dollars * 100).to_integral_value(ROUND_HALF_UP))
    await session.commit()
    return _redirect_to_dashboard(year, month, property_slug)


@router.get("/contract", response_class=HTMLResponse)
async def contract_page(
    request: Request,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    prop = await _get_property_or_404(session, property_slug or settings.property_slug)
    return templates.TemplateResponse(
        request,
        "contract.html",
        {
            "property": prop,
            "has_unresolved_blanks": has_unresolved_blanks(prop.contract_template or ""),
            "property_slug": property_slug,
        },
    )


# A fixed, obviously-fake booking so the preview is identical every time and
# can never be mistaken for a real one. Dates are far enough out that the
# balance date is unambiguous.
_PREVIEW_CHECK_IN = date(2027, 7, 9)
_PREVIEW_CHECK_OUT = date(2027, 7, 13)
_PREVIEW_TOTAL_CENTS = 203_721


def _preview_context() -> dict[str, str]:
    """Sample values matching the keys _quote_context passes at sign time.

    Kept deliberately in step with app/routes/booking_public.py: a key that
    exists here but not there would let a template pass preview and then fail
    for a real guest.
    """
    terms = compute_terms(total_cents=_PREVIEW_TOTAL_CENTS, check_in=_PREVIEW_CHECK_IN)

    def money(cents: int) -> str:
        return f"${cents / 100:,.2f}"

    def long(d: date) -> str:
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    return {
        "guest_name": "Sample Guest",
        "guest_email": "sample.guest@example.com",
        "guest_phone": "508-555-0147",
        "check_in": long(_PREVIEW_CHECK_IN),
        "check_out": long(_PREVIEW_CHECK_OUT),
        "check_in_iso": str(_PREVIEW_CHECK_IN),
        "check_out_iso": str(_PREVIEW_CHECK_OUT),
        "nights": str((_PREVIEW_CHECK_OUT - _PREVIEW_CHECK_IN).days),
        "total": money(terms.total_cents),
        "advance_payment": money(terms.advance_cents),
        "balance": money(terms.balance_cents),
        "balance_due_date": long(terms.balance_due),
        "security_deposit": money(terms.security_deposit_cents),
        "due_with_balance": money(terms.due_with_balance_cents),
        "deposit_return_days": str(terms.deposit_return_days),
    }


@router.post("/contract-template/load-standard")
async def load_standard_agreement(
    property_slug: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """Replace the saved agreement with the one versioned in the repo."""
    template = STANDARD_AGREEMENT_PATH.read_text(encoding="utf-8")
    await admin.update_contract_template(
        admin.ContractTemplateUpdate(template=template), property_slug, session
    )
    suffix = f"?property_slug={property_slug}" if property_slug else ""
    return RedirectResponse(url=f"/dashboard/contract{suffix}", status_code=303)


@router.get("/contract/preview", response_class=HTMLResponse)
async def contract_preview(
    request: Request,
    property_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """The saved agreement as a guest would see it, with sample values.

    Renders through the real guest template, so what you see here is what the
    booking page produces. A template that raises here would have 503'd for a
    guest; the error is shown rather than raised so the editor can fix it.
    """
    prop = await _get_property_or_404(session, property_slug or settings.property_slug)
    template = prop.contract_template or ""
    ctx = _preview_context()

    error: str | None = None
    if not template.strip():
        error = "No agreement has been saved yet."
    elif has_unresolved_blanks(template):
        error = (
            "The agreement still contains a square-bracket blank. Square brackets "
            "are not allowed anywhere in it, including in prose."
        )
    else:
        try:
            ctx["contract_text"] = render_contract(template, ctx)
        except Exception as exc:  # noqa: BLE001 - surfaced to the editor verbatim
            error = f"{type(exc).__name__}: {exc}"

    if error is not None:
        ctx["contract_text"] = ""

    return templates.TemplateResponse(
        request,
        "book_guest.html",
        {
            **ctx,
            "signed": False,
            "token": "preview",
            "preview": True,
            "preview_error": error,
            "preview_back": f"/dashboard/contract?property_slug={property_slug}"
            if property_slug
            else "/dashboard/contract",
        },
    )


@router.post("/contract-template")
async def dashboard_update_template(
    template: str = Form(...),
    return_to: str = Form(""),
    year: int | None = Form(None),
    month: int | None = Form(None),
    property_slug: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    payload = admin.ContractTemplateUpdate(template=template)
    await admin.update_contract_template(payload, property_slug, session)
    if return_to == "contract":
        suffix = f"?property_slug={property_slug}" if property_slug else ""
        return RedirectResponse(url=f"/dashboard/contract{suffix}", status_code=303)
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
