import pytest

from app.booking.quote import compute_quote


def test_quote_applies_14_percent_discount_no_tax():
    q = compute_quote(
        nightly_rates_cents=[20000, 20000, 20000, 20000],
        tax_enabled=False, tax_rate_percent=14.45,
    )
    assert q.nights == 4
    assert q.subtotal_cents == 80000
    assert q.discount_cents == 11200  # 14% of 80000
    assert q.tax_cents == 0
    assert q.total_cents == 68800  # 80000 - 11200


def test_quote_with_tax_enabled():
    q = compute_quote(
        nightly_rates_cents=[10000],
        tax_enabled=True, tax_rate_percent=14.45,
    )
    assert q.subtotal_cents == 10000
    assert q.discount_cents == 1400
    taxable = 10000 - 1400
    assert q.tax_cents == round(taxable * 0.1445)
    assert q.total_cents == taxable + q.tax_cents


def test_quote_rounds_to_nearest_cent():
    q = compute_quote(
        nightly_rates_cents=[333, 333, 333],
        tax_enabled=False, tax_rate_percent=14.45,
    )
    assert q.subtotal_cents == 999
    assert q.discount_cents == round(999 * 0.14)
    assert isinstance(q.total_cents, int)


def test_quote_sums_mixed_weekday_weekend_rates():
    rates = [65000, 99400, 99400, 45000]
    q = compute_quote(
        nightly_rates_cents=rates,
        tax_enabled=True, tax_rate_percent=14.45,
    )
    assert q.nights == 4
    assert q.subtotal_cents == sum(rates)
    discount = round(q.subtotal_cents * 0.14)
    assert q.discount_cents == discount
    taxable = q.subtotal_cents - discount
    assert q.tax_cents == round(taxable * 14.45 / 100)
    assert q.total_cents == taxable + q.tax_cents


def test_rejects_non_positive_stay():
    with pytest.raises(ValueError):
        compute_quote(
            nightly_rates_cents=[],
            tax_enabled=False, tax_rate_percent=14.45,
        )
