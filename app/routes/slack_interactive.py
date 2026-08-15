import asyncio
import json
import logging
from urllib.parse import unquote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.availability import assert_no_overlap
from app.booking.tokens import generate_booking_token
from app.config import settings
from app.db import get_session
from app.email.gmail import EmailSendError, send_email
from app.models import Property, Reservation
from app.slack.blocks import (
    build_approved_blocks,
    build_declined_blocks,
    build_overlap_error_blocks,
)
from app.slack.verify import SlackVerificationError, verify_slack_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["slack"])


@router.post("/slack/interactive")
async def slack_interactive(request: Request, session: AsyncSession = Depends(get_session)):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    try:
        verify_slack_signature(body=body, timestamp=timestamp, signature=signature)
    except (SlackVerificationError, UnicodeDecodeError):
        # A malformed (non-UTF-8) body makes verify_slack_signature raise
        # UnicodeDecodeError internally rather than SlackVerificationError —
        # treat it the same as any other verification failure (401), not an
        # unhandled 500.
        raise HTTPException(status_code=401, detail="invalid slack signature") from None

    form = dict(pair.split("=", 1) for pair in body.decode().split("&") if "=" in pair)
    payload = json.loads(unquote_plus(form.get("payload", "{}")))
    try:
        action = payload["actions"][0]
        action_id = action["action_id"]
        reservation_id = int(action["value"])
    except (KeyError, IndexError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="malformed interaction payload") from None

    reservation = (
        await session.execute(select(Reservation).where(Reservation.id == reservation_id))
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")
    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()

    if action_id == "booking_approve":
        return await _approve(session, reservation, prop)
    if action_id == "booking_decline":
        reservation.status = "canceled"
        await session.commit()
        return build_declined_blocks(property_name=prop.name, guest_name=reservation.guest_name or "guest")

    raise HTTPException(status_code=400, detail="unknown action_id")


async def _approve(session: AsyncSession, reservation: Reservation, prop: Property) -> dict:
    try:
        # Time has passed since the inquiry was created — re-check for a
        # conflict (another approval, a fresh Airbnb sync) before committing
        # to a hold, rather than trusting the state at inquiry time.
        await assert_no_overlap(session, prop.id, reservation.check_in, reservation.check_out)
    except HTTPException as exc:
        return build_overlap_error_blocks(f"dates now conflict — {exc.detail}")

    reservation.status = "hold"
    reservation.booking_token = generate_booking_token()
    await session.commit()

    link = f"{settings.public_base_url}/book/{reservation.booking_token}"
    subject = f"Your booking request at {prop.name}"
    body_text = (
        f"Hi {reservation.guest_name},\n\n"
        f"Your dates ({reservation.check_in} to {reservation.check_out}) are on hold. "
        f"Please review and sign the rental agreement, and see payment instructions, here:\n\n"
        f"{link}\n\nThanks!"
    )

    email_sent = True
    try:
        # smtplib is blocking; run off the event loop so a slow SMTP
        # handshake doesn't stall other requests (including Slack's own
        # 3-second interactivity timeout on unrelated in-flight requests).
        await asyncio.to_thread(
            send_email, to=reservation.guest_email, subject=subject, body=body_text
        )
    except EmailSendError:
        logger.exception("booking email failed for reservation #%s", reservation.id)
        email_sent = False

    return build_approved_blocks(
        property_name=prop.name, guest_name=reservation.guest_name or "guest",
        link=link, email_sent=email_sent,
    )
