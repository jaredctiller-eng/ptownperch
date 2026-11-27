import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import UndefinedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.availability import assert_no_overlap
from app.booking.contract import has_unresolved_blanks, render_contract
from app.booking.quote import compute_quote
from app.booking.rates import resolve_nightly_rates
from app.booking.terms import compute_terms

# The signature is drawn into a canvas and submitted as a data URL, then
# rendered back into an <img> on the signed page. Accept only what the pad
# actually produces: anything else is either a broken client or someone
# putting arbitrary content where an image is displayed.
SIGNATURE_PREFIX = "data:image/png;base64,"
MAX_SIGNATURE_CHARS = 2_000_000  # ~1.5 MB decoded; a 500x200 pad is far smaller
from app.config import settings
from app.db import get_session
from app.models import Property, Reservation
from app.routes.admin import _get_property_or_404
from app.slack.blocks import build_inquiry_blocks, build_signed_blocks
from app.slack.notify import post_slack_blocks

logger = logging.getLogger(__name__)

router = APIRouter(tags=["booking"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/book", response_class=HTMLResponse)
async def book_request_form(request: Request):
    return templates.TemplateResponse(request, "book_request.html")


@router.post("/book")
async def book_request_submit(
    check_in: date = Form(...),
    check_out: date = Form(...),
    guest_name: str = Form(...),
    guest_email: str = Form(...),
    guest_phone: str = Form(""),
    honeypot: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    if honeypot:
        # Silently pretend success so a bot can't tell its submission was
        # dropped; a human never sees or fills this hidden field.
        return RedirectResponse(url="/book/thanks", status_code=303)

    prop = await _get_property_or_404(session, settings.property_slug, for_update=True)
    if prop.nightly_rate_cents is None:
        raise HTTPException(status_code=503, detail="booking not configured yet")
    if check_out <= check_in:
        raise HTTPException(status_code=422, detail="check_out must be after check_in")

    await assert_no_overlap(session, prop.id, check_in, check_out)

    nightly_rates = await resolve_nightly_rates(
        session, prop.id, check_in, check_out, prop.nightly_rate_cents
    )
    quote = compute_quote(
        nightly_rates_cents=nightly_rates,
        tax_enabled=prop.direct_booking_tax_enabled,
        tax_rate_percent=prop.direct_booking_tax_rate_percent,
    )

    reservation = Reservation(
        property_id=prop.id,
        source="direct",
        status="inquiry",
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone or None,
        check_in=check_in,
        check_out=check_out,
    )
    session.add(reservation)
    await session.commit()
    await session.refresh(reservation)

    try:
        await post_slack_blocks(
            build_inquiry_blocks(
                property_name=prop.name, reservation_id=reservation.id, guest_name=guest_name,
                check_in=check_in, check_out=check_out, quote=quote,
            )
        )
    except Exception:
        logger.exception("slack post failed for inquiry #%s", reservation.id)

    return RedirectResponse(url="/book/thanks", status_code=303)


@router.get("/book/thanks", response_class=HTMLResponse)
async def book_thanks(request: Request):
    return templates.TemplateResponse(request, "book_thanks.html")


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _long_date(d) -> str:
    # "July 9, 2027" — a contract reads better than an ISO date, and the
    # guest is checking these against a text thread, not a calendar API.
    return f"{d.strftime('%B')} {d.day}, {d.year}"


async def _quote_context(session: AsyncSession, prop: Property, reservation: Reservation) -> dict:
    """Everything the contract template may reference.

    Direct bookings are priced by one flat agreed total (set at Slack
    approval); the payment schedule derives from it in app.booking.terms.
    `compute_quote` is NOT used here — it still serves the public /book
    inquiry page, which quotes from nightly rates before a human has
    negotiated anything.

    A reservation with no agreed total (an Airbnb row, or a direct one
    approved before this field existed) gets empty money strings rather
    than a crash; the signing route refuses to render such a contract.
    """
    nights = (reservation.check_out - reservation.check_in).days
    base = {
        "guest_name": reservation.guest_name or "",
        "guest_email": reservation.guest_email or "",
        "guest_phone": reservation.guest_phone or "",
        "check_in": _long_date(reservation.check_in),
        "check_out": _long_date(reservation.check_out),
        "check_in_iso": str(reservation.check_in),
        "check_out_iso": str(reservation.check_out),
        "nights": str(nights),
    }

    if reservation.agreed_total_cents is None:
        return {**base, "total": "", "advance_payment": "", "balance": "",
                "balance_due_date": "", "security_deposit": "",
                "due_with_balance": "", "deposit_return_days": ""}

    terms = compute_terms(
        total_cents=reservation.agreed_total_cents, check_in=reservation.check_in
    )
    return {
        **base,
        "total": _money(terms.total_cents),
        "advance_payment": _money(terms.advance_cents),
        "balance": _money(terms.balance_cents),
        "balance_due_date": _long_date(terms.balance_due),
        "security_deposit": _money(terms.security_deposit_cents),
        "due_with_balance": _money(terms.due_with_balance_cents),
        "deposit_return_days": str(terms.deposit_return_days),
    }


async def _get_reservation_by_token_or_404(session: AsyncSession, token: str) -> Reservation:
    reservation = (
        await session.execute(select(Reservation).where(Reservation.booking_token == token))
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown booking link")
    return reservation


@router.get("/book/{token}", response_class=HTMLResponse)
async def book_guest_page(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
):
    reservation = await _get_reservation_by_token_or_404(session, token)
    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()

    if reservation.signed_at is not None:
        # contract_snapshot is a frozen copy taken at signing time — render it
        # verbatim regardless of what the live template looks like now. A
        # later template edit (even one that reintroduces a blank) must never
        # change what an already-signed guest sees they agreed to.
        return templates.TemplateResponse(
            request, "book_guest.html",
            {
                "signed": True,
                "contract_text": reservation.contract_snapshot,
                "signed_name": reservation.signed_name,
                "signed_at": reservation.signed_at,
                # the drawn signature is part of the record; without it a
                # printed copy shows only a typed name
                "signature_image": reservation.signature_image,
                **(await _quote_context(session, prop, reservation)),
            },
        )

    # Hard stop: never let a guest reach a signable contract that still has
    # an operator-facing "[____]" blank in it (registration number, deposit
    # amount, etc). This must be enforced here, not just at the admin
    # template-editing endpoint, since the template can be edited at any time
    # after a booking link has already been sent out.
    # A direct booking with no agreed total has no payment schedule, so the
    # contract would render with blank money fields. Refuse, the same way we
    # refuse an unresolved "[____]".
    if reservation.agreed_total_cents is None:
        raise HTTPException(
            status_code=503,
            detail="this booking has no agreed total yet — it cannot be signed",
        )

    template = prop.contract_template or ""
    if has_unresolved_blanks(template):
        raise HTTPException(
            status_code=503,
            detail="contract template is incomplete — this booking cannot be signed yet",
        )

    try:
        contract_text = render_contract(template, await _quote_context(session, prop, reservation))
    except UndefinedError:
        # A template referencing a context key we don't provide is an
        # operator template bug, not a guest-facing crash.
        raise HTTPException(
            status_code=503,
            detail="contract template has an error — contact the operator",
        ) from None

    return templates.TemplateResponse(
        request, "book_guest.html",
        {
            "signed": False,
            "contract_text": contract_text,
            "token": token,
            **(await _quote_context(session, prop, reservation)),
        },
    )


@router.post("/book/{token}/sign")
async def book_guest_sign(
    token: str,
    request: Request,
    signed_name: str = Form(...),
    guest_home_address: str = Form(...),
    occupant_names: str = Form(...),
    signature_image: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    reservation = await _get_reservation_by_token_or_404(session, token)
    if reservation.status != "hold":
        raise HTTPException(status_code=409, detail="this hold is no longer active")
    if reservation.signed_at is not None:
        raise HTTPException(status_code=409, detail="already signed")

    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()
    template = prop.contract_template or ""
    if has_unresolved_blanks(template):
        # Same hard stop as the GET page — a direct POST must not be able to
        # bypass the blank-check just because it skipped the form render.
        raise HTTPException(
            status_code=503,
            detail="contract template is incomplete — this booking cannot be signed yet",
        )
    try:
        contract_text = render_contract(template, await _quote_context(session, prop, reservation))
    except UndefinedError:
        raise HTTPException(
            status_code=503,
            detail="contract template has an error — contact the operator",
        ) from None

    if not signature_image.startswith(SIGNATURE_PREFIX):
        raise HTTPException(status_code=400, detail="signature must be a PNG drawn on this page")
    if len(signature_image) > MAX_SIGNATURE_CHARS:
        raise HTTPException(status_code=413, detail="signature image is too large")

    reservation.signed_name = signed_name
    reservation.guest_home_address = guest_home_address
    reservation.occupant_names = occupant_names
    reservation.signature_image = signature_image
    reservation.signed_at = datetime.now(UTC)
    reservation.signed_ip = request.client.host if request.client else None
    reservation.contract_snapshot = contract_text
    await session.commit()

    try:
        nightly_rates = await resolve_nightly_rates(
            session, prop.id, reservation.check_in, reservation.check_out,
            prop.nightly_rate_cents or 0,
        )
        quote = compute_quote(
            nightly_rates_cents=nightly_rates,
            tax_enabled=prop.direct_booking_tax_enabled,
            tax_rate_percent=prop.direct_booking_tax_rate_percent,
        )
        await post_slack_blocks(
            build_signed_blocks(
                property_name=prop.name,
                guest_name=reservation.guest_name or "guest",
                reservation_id=reservation.id,
                total_cents=quote.total_cents,
            )
        )
    except Exception:
        logger.exception("slack post failed for signed reservation #%s", reservation.id)

    return RedirectResponse(url=f"/book/{token}", status_code=303)
