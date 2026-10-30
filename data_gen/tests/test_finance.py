"""The calculator is the source of truth for loan-maths answers, so it gets the strictest tests:
published reference values, invariants over many loans, and every invalid input."""
import math

import pytest

from data_gen.finance import (balance_after, emi, fee_with_gst, foir_pct, ltv_pct, months_to_repay, paise,
                              prepayment_effect, schedule, total_interest)


# ---- reference values ---------------------------------------------------------------------------
@pytest.mark.parametrize("principal, rate, months, expected", [
    (100_000, 12, 12, 8_884.88),        # textbook example: 1 lakh at 12% for a year
    (1_000_000, 10, 60, 21_247.05),     # 10 lakh at 10% for 5 years (exact 21,247.0447, rounded up)
    (4_000_000, 8.75, 240, 35_348.43),  # 40 lakh at 8.75% for 20 years
    (500_000, 0, 24, 20_833.34),        # zero interest: principal / months, rounded up
])
def test_emi_reference_values(principal, rate, months, expected):
    assert emi(principal, rate, months) == expected


def test_emi_matches_the_closed_form():
    p, r, n = 2_500_000, 9.1 / 1200, 180
    exact = p * r * (1 + r) ** n / ((1 + r) ** n - 1)
    assert 0 <= emi(2_500_000, 9.1, 180) - exact < 0.01  # rounded up, never by a full paisa or more


# ---- schedule invariants across many loans --------------------------------------------------------
LOANS = [(p, rate, n) for p in (50_000, 1_000_000, 7_500_000) for rate in (0, 7.5, 10.4, 18) for n in (6, 60, 240)]


@pytest.mark.parametrize("principal, rate, months", LOANS)
def test_schedule_ends_exactly_on_time_and_repays_the_principal(principal, rate, months):
    rows = schedule(principal, rate, emi(principal, rate, months))
    assert len(rows) == months
    assert rows[-1].balance == 0
    drift = 0.005 * months + 0.01  # each displayed row is rounded to the paisa
    assert sum(r.principal_paid for r in rows) == pytest.approx(principal, abs=drift)
    assert sum(r.payment for r in rows) == pytest.approx(principal + sum(r.interest for r in rows), abs=drift)
    assert all(r.balance >= 0 for r in rows)
    assert all(a.balance > b.balance for a, b in zip(rows, rows[1:]))  # strictly falling


def test_rounding_never_adds_a_month_and_the_last_instalment_is_only_slightly_smaller():
    rows = schedule(7_500_000, 18, emi(7_500_000, 18, 240))
    assert len(rows) == 240
    assert 0 <= rows[0].payment - rows[-1].payment < 25  # rounding up by under a paisa a month, over 240 months


def test_balance_after():
    instalment = emi(4_000_000, 8.75, 240)
    rows = schedule(4_000_000, 8.75, instalment)
    assert balance_after(4_000_000, 8.75, instalment, 0) == 4_000_000
    assert balance_after(4_000_000, 8.75, instalment, 36) == rows[35].balance == pytest.approx(3_746_564.28, abs=1)
    assert balance_after(4_000_000, 8.75, instalment, 500) == 0


def test_months_to_repay_agrees_with_the_closed_form():
    balance, rate, instalment = 3_246_564.28, 8.75, 35_348.43
    r = rate / 1200
    exact = -math.log(1 - balance * r / instalment) / math.log(1 + r)
    assert months_to_repay(balance, rate, instalment) == math.ceil(exact) == 153


def test_total_interest():
    instalment = emi(100_000, 12, 12)
    assert total_interest(100_000, 12, instalment) == pytest.approx(12 * instalment - 100_000, abs=0.1)


# ---- prepayment: the playground's hard example ---------------------------------------------------
def test_prepayment_keeping_emi_matches_the_worked_example():
    r = prepayment_effect(4_000_000, 8.75, 240, after_payments=36, prepayment=500_000, keep="emi")
    assert r.emi == 35_348.43 and r.new_emi == r.emi
    assert r.balance_before == pytest.approx(3_746_564.28, abs=1)
    assert r.balance_after_prepayment == pytest.approx(3_246_564.28, abs=1)
    assert (r.remaining_payments, r.total_months, r.months_saved) == (153, 189, 51)
    assert r.interest_saved == pytest.approx(1_321_330, abs=5)


def test_prepayment_keeping_tenure_lowers_the_emi_instead():
    by_emi = prepayment_effect(4_000_000, 8.75, 240, 36, 500_000, keep="emi")
    by_tenure = prepayment_effect(4_000_000, 8.75, 240, 36, 500_000, keep="tenure")
    assert by_tenure.total_months == 240 and by_tenure.months_saved == 0
    assert by_tenure.new_emi == pytest.approx(30_630.98, abs=0.02)
    assert by_tenure.new_emi < by_tenure.emi
    assert 0 < by_tenure.interest_saved < by_emi.interest_saved  # shortening the loan saves more interest


@pytest.mark.parametrize("small, large", [(100_000, 500_000), (500_000, 1_500_000)])
def test_a_larger_prepayment_never_saves_less(small, large):
    a = prepayment_effect(4_000_000, 8.75, 240, 36, small)
    b = prepayment_effect(4_000_000, 8.75, 240, 36, large)
    assert b.months_saved > a.months_saved and b.interest_saved > a.interest_saved


# ---- ratios and fees ---------------------------------------------------------------------------
def test_foir_ltv_fee():
    assert foir_pct([22_000, 15_000], 90_000) == 41.11
    assert ltv_pct(4_000_000, 5_000_000) == 80.0
    assert fee_with_gst(2_500_000, 0.5) == 14_750.0  # 12,500 fee + 18% GST


def test_paise_rounds_half_away_from_zero():
    assert paise(0.125) == 0.13 and paise(-0.125) == -0.13 and paise(2.004) == 2.0


# ---- invalid inputs ------------------------------------------------------------------------------
@pytest.mark.parametrize("call", [
    lambda: emi(0, 10, 12),
    lambda: emi(100_000, -1, 12),
    lambda: emi(100_000, 10, 0),
    lambda: emi(100_000, 10, 12.5),
    lambda: schedule(1_000_000, 12, 10_000),                       # 10,000 does not cover 10,000 interest
    lambda: prepayment_effect(1_000_000, 10, 60, 0, 10_000),        # before the first instalment
    lambda: prepayment_effect(1_000_000, 10, 60, 60, 10_000),       # after the loan ended
    lambda: prepayment_effect(1_000_000, 10, 60, 12, 5_000_000),    # more than is owed
    lambda: prepayment_effect(1_000_000, 10, 60, 12, 10_000, keep="both"),
    lambda: prepayment_effect(1_000_000, 10, 60, 12, -5),
    lambda: foir_pct([10_000], 0),
    lambda: foir_pct([-1], 50_000),
    lambda: ltv_pct(1, 0),
    lambda: fee_with_gst(0, 1),
    lambda: balance_after(100_000, 10, 9_000, -1),
])
def test_invalid_inputs_are_rejected(call):
    with pytest.raises(ValueError):
        call()
