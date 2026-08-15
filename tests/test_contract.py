import pytest
from jinja2 import UndefinedError

from app.booking.contract import has_unresolved_blanks, render_contract


def test_detects_unresolved_blanks():
    assert has_unresolved_blanks("Deposit: $[____] due at booking.") is True
    assert has_unresolved_blanks("Guest: {{guest_name}}. All blanks filled.") is False


def test_renders_booking_specific_placeholders():
    template = "Guest {{guest_name}} stays {{check_in}} to {{check_out}} for {{total}}."
    rendered = render_contract(
        template,
        {"guest_name": "Alice", "check_in": "2026-09-01", "check_out": "2026-09-05", "total": "$688.00"},
    )
    assert rendered == "Guest Alice stays 2026-09-01 to 2026-09-05 for $688.00."


def test_missing_placeholder_raises_loudly():
    # A silently-blank field in a signed legal document is worse than a
    # crash the operator sees before the guest ever gets the link.
    with pytest.raises(UndefinedError):
        render_contract("Guest: {{guest_name}}, Total: {{missing_key}}", {"guest_name": "Alice"})
