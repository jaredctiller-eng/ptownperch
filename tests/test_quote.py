from datetime import date

from app.booking.quote import compute_quote


def test_quote_applies_14_percent_discount_no_tax():
    q = compute_quote(
        nightly_rate_cents=20000, check_in=date(2026, 9, 1), check_out=date(2026, 9, 5),
        tax_enabled=False, tax_rate_percent=14.45,
    )
    assert q.nights == 4
    assert q.subtotal_cents == 80000
    assert q.discount_cents == 11200  # 14% of 80000
    assert q.tax_cents == 0
    assert q.total_cents == 68800  # 80000 - 11200


def test_quote_with_tax_enabled():
    q = compute_quote(
        nightly_rate_cents=10000, check_in=date(2026, 9, 1), check_out=date(2026, 9, 2),
        tax_enabled=True, tax_rate_percent=14.45,
    )
    assert q.subtotal_cents == 10000
    assert q.discount_cents == 1400
    taxable = 10000 - 1400
    assert q.tax_cents == round(taxable * 0.1445)
    assert q.total_cents == taxable + q.tax_cents


def test_quote_rounds_to_nearest_cent():
    q = compute_quote(
        nightly_rate_cents=333, check_in=date(2026, 9, 1), check_out=date(2026, 9, 4),
        tax_enabled=False, tax_rate_percent=14.45,
    )
    assert q.subtotal_cents == 999
    assert q.discount_cents == round(999 * 0.14)
    assert isinstance(q.total_cents, int)
