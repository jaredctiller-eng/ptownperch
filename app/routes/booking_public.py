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

    quote = compute_quote(
        nightly_rate_cents=prop.nightly_rate_cents,
        check_in=check_in,
        check_out=check_out,
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


def _quote_context(prop: Property, reservation: Reservation) -> dict:
    quote = compute_quote(
        nightly_rate_cents=prop.nightly_rate_cents or 0,
        check_in=reservation.check_in,
        check_out=reservation.check_out,
        tax_enabled=prop.direct_booking_tax_enabled,
        tax_rate_percent=prop.direct_booking_tax_rate_percent,
    )
    return {
        "guest_name": reservation.guest_name or "",
        "check_in": str(reservation.check_in),
        "check_out": str(reservation.check_out),
        "total": f"${quote.total_cents / 100:.2f}",
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
                **_quote_context(prop, reservation),
            },
        )

    # Hard stop: never let a guest reach a signable contract that still has
    # an operator-facing "[____]" blank in it (registration number, deposit
    # amount, etc). This must be enforced here, not just at the admin
    # template-editing endpoint, since the template can be edited at any time
    # after a booking link has already been sent out.
    template = prop.contract_template or ""
    if has_unresolved_blanks(template):
        raise HTTPException(
            status_code=503,
            detail="contract template is incomplete — this booking cannot be signed yet",
        )

    try:
        contract_text = render_contract(template, _quote_context(prop, reservation))
    except UndefinedError:
        # A template referencing a context key we don't provide is an
        # operator template bug, not a guest-facing crash.
        raise HTTPException(
            status_code=503,
            detail="contract template has an error — contact the operator",
        ) from None

    return templates.TemplateResponse(
        request, "book_guest.html",
        {"signed": False, "contract_text": contract_text, "token": token, **_quote_context(prop, reservation)},
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
        contract_text = render_contract(template, _quote_context(prop, reservation))
    except UndefinedError:
        raise HTTPException(
            status_code=503,
            detail="contract template has an error — contact the operator",
        ) from None

    reservation.signed_name = signed_name
    reservation.guest_home_address = guest_home_address
    reservation.occupant_names = occupant_names
    reservation.signature_image = signature_image
    reservation.signed_at = datetime.now(UTC)
    reservation.signed_ip = request.client.host if request.client else None
    reservation.contract_snapshot = contract_text
    await session.commit()

    try:
        quote = compute_quote(
            nightly_rate_cents=prop.nightly_rate_cents or 0,
            check_in=reservation.check_in,
            check_out=reservation.check_out,
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
