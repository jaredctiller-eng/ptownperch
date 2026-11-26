import asyncio
import json
import logging
from urllib.parse import unquote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.availability import assert_no_overlap
from app.booking.mark_paid import MarkPaidError
from app.booking.mark_paid import mark_paid as apply_mark_paid
from app.booking.terms import TermsError, parse_total_to_cents
from app.booking.tokens import generate_booking_token
from app.config import settings
from app.db import get_session
from app.email.gmail import EmailSendError, send_email
from app.models import Property, Reservation
from app.routes.admin import _get_property_or_404
from app.slack.blocks import (
    build_approved_blocks,
    build_declined_blocks,
    build_mark_paid_confirmed_blocks,
    build_mark_paid_error_blocks,
    build_overlap_error_blocks,
    build_total_error_blocks,
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

    # Whatever the operator typed into the message's input block, if anything.
    # Absent for Decline/Mark Paid and for any older message posted before the
    # input block existed — those stay working, so a message already sitting in
    # the channel does not break on deploy.
    agreed_total_raw = (
        payload.get("state", {})
        .get("values", {})
        .get("agreed_total", {})
        .get("value", {})
        .get("value")
    )

    reservation = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id,
                Reservation.source == "direct",
            )
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=404, detail="unknown reservation")
    prop = (
        await session.execute(select(Property).where(Property.id == reservation.property_id))
    ).scalar_one()

    if action_id == "booking_approve":
        return await _approve(session, reservation, prop, agreed_total_raw)
    if action_id == "booking_decline":
        reservation.status = "canceled"
        await session.commit()
        return build_declined_blocks(property_name=prop.name, guest_name=reservation.guest_name or "guest")
    if action_id == "booking_mark_paid":
        try:
            await apply_mark_paid(session, reservation)
        except MarkPaidError as exc:
            return build_mark_paid_error_blocks(str(exc))
        return build_mark_paid_confirmed_blocks(
            property_name=prop.name, guest_name=reservation.guest_name or "guest"
        )

    raise HTTPException(status_code=400, detail="unknown action_id")


async def _approve(
    session: AsyncSession,
    reservation: Reservation,
    prop: Property,
    agreed_total_raw: str | None,
) -> dict:
    # Re-fetch the Property row WITH a lock before the overlap check. `prop`
    # as passed in was read without a lock by the dispatcher above, which
    # leaves a TOCTOU window: two Approve actions processed concurrently
    # (two overlapping inquiries approved near-simultaneously, or a Slack
    # redelivery — plausible here since this handler does a blocking-then-
    # threaded SMTP send before responding) could both pass the overlap
    # check and both commit. Reuse the same locking helper the admin routes
    # use rather than duplicating the query; `prop.slug` is already loaded
    # from the unlocked fetch, so no signature change is needed here.
    # Validate the agreed total before anything else: a typo must leave the
    # reservation exactly as it was, not half-approved with a bad number.
    # Re-approving an already-priced reservation without retyping the total
    # keeps the stored one.
    try:
        if agreed_total_raw is not None and agreed_total_raw.strip():
            total_cents = parse_total_to_cents(agreed_total_raw)
        elif reservation.agreed_total_cents is not None:
            total_cents = reservation.agreed_total_cents
        else:
            raise TermsError("no agreed total was entered")
    except TermsError as exc:
        return build_total_error_blocks(str(exc))

    prop = await _get_property_or_404(session, prop.slug, for_update=True)
    try:
        # Time has passed since the inquiry was created — re-check for a
        # conflict (another approval, a fresh Airbnb sync) before committing
        # to a hold, rather than trusting the state at inquiry time.
        await assert_no_overlap(session, prop.id, reservation.check_in, reservation.check_out)
    except HTTPException as exc:
        return build_overlap_error_blocks(f"dates now conflict — {exc.detail}")

    reservation.status = "hold"
    reservation.agreed_total_cents = total_cents
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
