import logging
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.availability import assert_no_overlap
from app.booking.quote import compute_quote
from app.config import settings
from app.db import get_session
from app.models import Reservation
from app.routes.admin import _get_property_or_404
from app.slack.blocks import build_inquiry_blocks
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
