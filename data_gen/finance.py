"""Deterministic loan arithmetic: the source of truth for every loan-maths answer.

Used two ways: to generate correct answers for the benchmark, and to grade model answers.
An LLM may interpret the question; the numbers are always computed here.

Conventions (stated so every answer is reproducible):
- Rates are annual percentages (8.75 means 8.75% a year), compounded monthly: r = rate / 12 / 100.
- Reducing-balance loans with equal monthly instalments (EMI), paid at the end of each month.
- EMI is rounded UP to the next paisa, as lenders do, so rounding can never leave an extra month.
- Interest and balances are carried unrounded; schedule rows show them rounded to the paisa.
- The last instalment pays only what is left, so it can be slightly smaller; it still counts as one month.
- A prepayment is applied immediately after the stated instalment.
"""
import math
from dataclasses import dataclass

MAX_MONTHS = 1200  # 100 years: stops runaway loops on bad inputs
SETTLED = 0.005    # rupees: a balance below half a paisa is floating-point dust, treated as zero


def _check(principal: float, annual_rate_pct: float, months: int | None = None) -> None:
    if principal <= 0:
        raise ValueError("principal must be positive")
    if annual_rate_pct < 0:
        raise ValueError("interest rate cannot be negative")
    if months is not None and (months <= 0 or int(months) != months):
        raise ValueError("months must be a positive whole number")


def monthly_rate(annual_rate_pct: float) -> float:
    return annual_rate_pct / 12 / 100


def paise(x: float) -> float:
    """Round half away from zero to 2 decimals (avoids banker's rounding surprises)."""
    return math.floor(abs(x) * 100 + 0.5) / 100 * (1 if x >= 0 else -1)


def _ceil_paise(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    """Equal monthly instalment, rounded up to the next paisa."""
    _check(principal, annual_rate_pct, months)
    r = monthly_rate(annual_rate_pct)
    if r == 0:
        return _ceil_paise(principal / months)
    growth = (1 + r) ** months
    return _ceil_paise(principal * r * growth / (growth - 1))


@dataclass(frozen=True)
class Row:
    month: int
    payment: float
    interest: float
    principal_paid: float
    balance: float


def schedule(principal: float, annual_rate_pct: float, instalment: float,
             max_months: int = MAX_MONTHS) -> list[Row]:
    """Month-by-month amortisation until the balance reaches zero."""
    _check(principal, annual_rate_pct)
    r = monthly_rate(annual_rate_pct)
    if instalment <= principal * r:
        raise ValueError("the instalment does not cover the monthly interest, so the loan never ends")
    rows, balance, month = [], float(principal), 0
    while balance > SETTLED:
        month += 1
        if month > max_months:
            raise ValueError("loan does not finish within the month limit")
        interest = balance * r
        payment = min(instalment, balance + interest)
        balance = balance + interest - payment
        if balance <= SETTLED:
            balance = 0.0
        rows.append(Row(month, round(payment, 2), round(interest, 2), round(payment - interest, 2), round(balance, 2)))
    return rows


def balance_after(principal: float, annual_rate_pct: float, instalment: float, payments: int) -> float:
    """Outstanding principal right after the given number of instalments."""
    if payments < 0:
        raise ValueError("payments cannot be negative")
    if payments == 0:
        return round(principal, 2)
    rows = schedule(principal, annual_rate_pct, instalment)
    return rows[payments - 1].balance if payments <= len(rows) else 0.0


def months_to_repay(balance: float, annual_rate_pct: float, instalment: float) -> int:
    """Number of instalments needed, counting a smaller last one as a month."""
    return len(schedule(balance, annual_rate_pct, instalment))


def total_interest(principal: float, annual_rate_pct: float, instalment: float) -> float:
    return round(sum(row.interest for row in schedule(principal, annual_rate_pct, instalment)), 2)


@dataclass(frozen=True)
class PrepaymentResult:
    emi: float                       # original EMI
    balance_before: float            # right after `after_payments` instalments
    balance_after_prepayment: float
    keep: str                        # "emi" (shorter loan) or "tenure" (smaller EMI)
    new_emi: float                   # equals emi when keep == "emi"
    remaining_payments: int          # instalments still to pay after the prepayment
    total_months: int                # when the loan now ends, counted from the start
    months_saved: int
    interest_original: float
    interest_new: float
    interest_saved: float


def prepayment_effect(principal: float, annual_rate_pct: float, months: int, after_payments: int,
                      prepayment: float, keep: str = "emi") -> PrepaymentResult:
    """Part-prepayment after `after_payments` instalments. keep="emi" keeps the instalment and shortens
    the loan; keep="tenure" keeps the end date and lowers the instalment."""
    _check(principal, annual_rate_pct, months)
    if keep not in ("emi", "tenure"):
        raise ValueError("keep must be 'emi' or 'tenure'")
    if not 0 < after_payments < months:
        raise ValueError("the prepayment must happen during the loan")
    if prepayment <= 0:
        raise ValueError("prepayment must be positive")
    instalment = emi(principal, annual_rate_pct, months)
    original = schedule(principal, annual_rate_pct, instalment)
    before = original[after_payments - 1].balance  # rounded to the paisa, as a statement would show it
    if prepayment >= before:
        raise ValueError("the prepayment would close the loan; use a smaller amount")
    after = round(before - prepayment, 2)
    paid_interest = round(sum(row.interest for row in original[:after_payments]), 2)
    interest_original = round(sum(row.interest for row in original), 2)

    if keep == "emi":
        new_instalment = instalment
        rest = schedule(after, annual_rate_pct, instalment)
    else:
        new_instalment = emi(after, annual_rate_pct, len(original) - after_payments)
        rest = schedule(after, annual_rate_pct, new_instalment)
    interest_new = round(paid_interest + sum(row.interest for row in rest), 2)
    total = after_payments + len(rest)
    return PrepaymentResult(
        emi=instalment, balance_before=before, balance_after_prepayment=after, keep=keep,
        new_emi=new_instalment, remaining_payments=len(rest), total_months=total,
        months_saved=len(original) - total, interest_original=interest_original,
        interest_new=interest_new, interest_saved=round(interest_original - interest_new, 2),
    )


def foir_pct(monthly_obligations: list[float], monthly_income: float) -> float:
    """Fixed obligation to income ratio, in percent, rounded to 2 decimals."""
    if monthly_income <= 0:
        raise ValueError("monthly income must be positive")
    if any(x < 0 for x in monthly_obligations):
        raise ValueError("obligations cannot be negative")
    return round(100 * sum(monthly_obligations) / monthly_income, 2)


def ltv_pct(loan_amount: float, property_value: float) -> float:
    """Loan-to-value ratio, in percent, rounded to 2 decimals."""
    if property_value <= 0 or loan_amount < 0:
        raise ValueError("values must be positive")
    return round(100 * loan_amount / property_value, 2)


def fee_with_gst(loan_amount: float, fee_pct: float, gst_pct: float = 18.0) -> float:
    """Processing fee as a percent of the loan, plus GST on the fee, rounded to the paisa."""
    if loan_amount <= 0 or fee_pct < 0 or gst_pct < 0:
        raise ValueError("invalid amount or percentage")
    fee = loan_amount * fee_pct / 100
    return paise(fee * (1 + gst_pct / 100))
