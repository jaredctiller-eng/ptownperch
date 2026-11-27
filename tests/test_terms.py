"""Payment terms derived from the agreed total.

These numbers go into a contract a guest signs, so the properties that
matter are exactness and refusal — never a silent wrong amount.
"""
from datetime import date

import pytest

from app.booking.terms import (
    SECURITY_DEPOSIT_CENTS,
    TermsError,
    compute_terms,
    parse_total_to_cents,
    rent_and_excise_cents,
)

# --- parsing: what an operator actually types into Slack -------------------

@pytest.mark.parametrize("raw,cents", [
    ("2000", 200000),
    ("2000.00", 200000),
    ("$2000", 200000),
    ("$2,000.00", 200000),
    (" 2,450.50 ", 245050),
    ("0.01", 1),
    ("1995.99", 199599),
])
def test_parses_the_shapes_people_type(raw, cents):
    assert parse_total_to_cents(raw) == cents


@pytest.mark.parametrize("raw", [
    "", "   ", None,
    "two grand", "2000 dollars", "abc",
    "0", "-100", "-$50",
    "2000.001",          # more precision than cents
    "200000000",         # typo territory, over the ceiling
])
def test_refuses_anything_ambiguous(raw):
    with pytest.raises(TermsError):
        parse_total_to_cents(raw)


# --- the split: must never lose or invent a cent ---------------------------

def test_advance_and_balance_sum_to_the_total():
    terms = compute_terms(total_cents=200000, check_in=date(2027, 7, 9))
    assert terms.advance_cents == 100000
    assert terms.balance_cents == 100000
    assert terms.advance_cents + terms.balance_cents == terms.total_cents


@pytest.mark.parametrize("total", [1, 3, 99, 101, 12345, 199999, 200001, 787877])
def test_split_is_exact_for_odd_amounts(total):
    """An odd number of cents cannot round into a gap or an overlap."""
    terms = compute_terms(total_cents=total, check_in=date(2027, 7, 9))
    assert terms.advance_cents + terms.balance_cents == total
    assert terms.advance_cents >= 0
    assert terms.balance_cents >= 0


def test_balance_is_due_thirty_days_before_check_in():
    terms = compute_terms(total_cents=200000, check_in=date(2027, 7, 9))
    assert terms.balance_due == date(2027, 6, 9)


def test_balance_due_crosses_a_year_boundary():
    terms = compute_terms(total_cents=150000, check_in=date(2027, 1, 15))
    assert terms.balance_due == date(2026, 12, 16)


def test_security_deposit_is_flat_and_on_top():
    terms = compute_terms(total_cents=200000, check_in=date(2027, 7, 9))
    assert terms.security_deposit_cents == SECURITY_DEPOSIT_CENTS == 50000
    # The deposit is NOT part of the total — it is extra, and refundable.
    assert terms.advance_cents + terms.balance_cents == terms.total_cents
    assert terms.due_with_balance_cents == terms.balance_cents + 50000


def test_zero_or_negative_total_refused():
    for bad in (0, -1, -200000):
        with pytest.raises(TermsError):
            compute_terms(total_cents=bad, check_in=date(2027, 7, 9))


# --- bookkeeping split (not guest-facing) ----------------------------------

def test_rent_and_excise_sum_back_to_the_total():
    for total in (200000, 199999, 1, 123456, 787877):
        rent, excise = rent_and_excise_cents(total)
        assert rent + excise == total
        assert rent > 0


def test_excise_is_about_fourteen_point_four_five_percent_of_rent():
    rent, excise = rent_and_excise_cents(200000)
    assert rent == 174749          # 2000 / 1.1445
    assert excise == 25251
    assert abs(excise / rent - 0.1445) < 0.0001
