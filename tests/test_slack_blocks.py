from datetime import date

from app.booking.quote import compute_quote
from app.slack.blocks import build_declined_blocks, build_inquiry_blocks, build_overlap_error_blocks


def _quote():
    return compute_quote(
        nightly_rate_cents=20000, check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        tax_enabled=False, tax_rate_percent=14.45,
    )


def test_inquiry_blocks_carry_reservation_id_and_actions():
    payload = build_inquiry_blocks(
        property_name="Ptown Perch", reservation_id=42, guest_name="Alice",
        check_in=date(2026, 9, 1), check_out=date(2026, 9, 5), quote=_quote(),
    )
    actions = payload["blocks"][-1]["elements"]
    assert {a["action_id"] for a in actions} == {"booking_approve", "booking_decline"}
    assert all(a["value"] == "42" for a in actions)
    text = payload["blocks"][0]["text"]["text"]
    assert "Alice" in text and "$688.00" in text


def test_declined_blocks_have_no_actions():
    payload = build_declined_blocks(property_name="Ptown Perch", guest_name="Alice")
    assert all(b["type"] != "actions" for b in payload["blocks"])


def test_overlap_error_blocks_include_message():
    payload = build_overlap_error_blocks("dates now conflict with reservation #7")
    assert "dates now conflict" in payload["blocks"][0]["text"]["text"]
