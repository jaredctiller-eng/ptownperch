from datetime import date

from app.booking.quote import Quote


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def build_inquiry_blocks(
    *, property_name: str, reservation_id: int, guest_name: str,
    check_in: date, check_out: date, quote: Quote,
) -> dict:
    return {
        "text": f"New direct booking inquiry at {property_name} from {guest_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":email: *New direct booking inquiry* at {property_name}\n"
                        f"{guest_name} — {check_in} → {check_out} ({quote.nights} nights)\n"
                        f"Quote: {_money(quote.total_cents)} "
                        f"(avg {_money(quote.subtotal_cents // quote.nights)}/night, "
                        f"discount -{_money(quote.discount_cents)})"
                    ),
                },
            },
            {
                # An input block in the MESSAGE (not a modal): its typed value
                # arrives in payload["state"]["values"] when a button in the
                # same message is clicked. A modal would need views.open and
                # therefore a bot token, which this app does not have — it
                # posts via an incoming webhook and only verifies signatures.
                "type": "input",
                "block_id": "agreed_total",
                "optional": True,
                "label": {"type": "plain_text", "text": "Agreed total (what the guest pays, all in)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "e.g. 2000 or $2,000.00"},
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "booking_approve",
                        "value": str(reservation_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Decline"},
                        "style": "danger",
                        "action_id": "booking_decline",
                        "value": str(reservation_id),
                    },
                ],
            },
        ],
    }


def build_approved_blocks(*, property_name: str, guest_name: str, link: str, email_sent: bool) -> dict:
    status_line = (
        f":white_check_mark: Emailed the booking link to {guest_name}."
        if email_sent
        else ":warning: Email send failed — send this link to the guest yourself:"
    )
    return {
        "text": f"Approved: {guest_name} at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *Approved* — {guest_name} at {property_name}\n"
                        f"{status_line}\n{link}"
                    ),
                },
            }
        ],
    }


def build_declined_blocks(*, property_name: str, guest_name: str) -> dict:
    return {
        "text": f"Declined: {guest_name} at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":x: *Declined* — {guest_name} at {property_name}",
                },
            }
        ],
    }


def build_overlap_error_blocks(message: str) -> dict:
    return {
        "text": f"Could not approve: {message}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":rotating_light: *Could not approve* — {message}. Decline this inquiry instead.",
                },
            }
        ],
    }


def build_signed_blocks(*, property_name: str, guest_name: str, reservation_id: int, total_cents: int) -> dict:
    return {
        "text": f"{guest_name} signed the agreement at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":pencil2: *{guest_name} signed the rental agreement* at {property_name}\n"
                        f"Total due: {_money(total_cents)}. Tap Mark Paid once you see the Venmo/Zelle payment land."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Mark Paid"},
                        "style": "primary",
                        "action_id": "booking_mark_paid",
                        "value": str(reservation_id),
                    }
                ],
            },
        ],
    }


def build_mark_paid_confirmed_blocks(*, property_name: str, guest_name: str) -> dict:
    return {
        "text": f"Confirmed: {guest_name} at {property_name}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":tada: *Confirmed* — {guest_name} at {property_name} is paid and confirmed.",
                },
            }
        ],
    }


def build_mark_paid_error_blocks(message: str) -> dict:
    return {
        "text": f"Could not mark paid: {message}",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":warning: *Could not mark paid* — {message}"},
            }
        ],
    }


def build_total_error_blocks(reason: str) -> dict:
    """Approve was clicked with an unusable total — nothing was changed."""
    return {
        "text": f"Could not approve: {reason}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *Not approved* — {reason}.\n"
                        "Nothing was changed. Fix the agreed total and click Approve again."
                    ),
                },
            }
        ],
    }
