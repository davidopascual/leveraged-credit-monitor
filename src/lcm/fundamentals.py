"""Issuer credit metrics from XBRL: debt, LTM EBITDA, LTM interest, maturities, ratios.

Definitions (stated once, used everywhere)
- Total debt: carrying amount of long-term debt including current maturities,
  plus short-term borrowings (e.g. receivables securitization, bank loans) when
  the company reports them separately, as of the latest balance sheet (10-Q or
  10-K, whichever is newer). Book value, not market.
- EBITDA (LTM): GAAP operating income + depreciation & amortization, last twelve
  months. Unadjusted: no add-backs for impairments, restructuring, stock comp, etc.
  This is NOT the company's or the lenders' "Adjusted EBITDA".
- Interest (LTM): interest expense, last twelve months. If a company only tags a
  net figure (interest expense net of interest income), we use it and say so.
- Debt / EBITDA and EBITDA / interest: not meaningful (NaN) if the denominator <= 0.

XBRL tags differ by company, so each field has an ordered list of candidate tags.
The first tag that has a value for the needed period wins, and we record which.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from lcm import xbrl


@dataclass(frozen=True)
class Candidate:
    tag: str
    sign: float = 1.0       # -1 for "net" tags reported as negative expense
    note: str = ""


DEBT_TAGS = [
    Candidate("LongTermDebt"),
    Candidate("DebtLongtermAndShorttermCombinedAmount"),
    Candidate("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"),
    Candidate("DebtInstrumentCarryingAmount", note="instrument carrying amount"),
]
# By the US-GAAP taxonomy definition, LongTermDebt excludes short-term borrowings,
# so adding these does not double count.
SHORT_TERM_DEBT_TAGS = [
    Candidate("ShortTermBorrowings"),
    Candidate("ShortTermBankLoansAndNotesPayable"),
]
OPERATING_INCOME_TAGS = [Candidate("OperatingIncomeLoss")]
DA_TAGS = [
    Candidate("DepreciationDepletionAndAmortization"),
    Candidate("DepreciationAmortizationAndAccretionNet"),
    Candidate("DepreciationAndAmortization"),
    Candidate("DepreciationAmortizationAndOther", note="includes 'other' per company tag"),
]
INTEREST_TAGS = [
    Candidate("InterestExpense"),
    Candidate("InterestExpenseNonoperating"),
    Candidate("InterestExpenseDebt"),
    Candidate("InterestAndDebtExpense"),
    Candidate("InterestIncomeExpenseNonoperatingNet", sign=-1.0, note="net of interest income"),
    Candidate("InterestIncomeExpenseNet", sign=-1.0, note="net of interest income"),
]
MATURITY_TAGS = {
    "remainder": "LongTermDebtMaturitiesRepaymentsOfPrincipalRemainderOfFiscalYear",
    1: "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    2: "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
    3: "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",
    4: "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",
    5: "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",
    "after5": "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
}


@dataclass
class Resolved:
    field: str
    value: float            # USD; NaN if missing
    tag: str | None         # None if every candidate was missing
    detail: str = ""

    @property
    def missing(self) -> bool:
        return self.tag is None


# --------------------------------------------------------------------------
# Tag resolution
# --------------------------------------------------------------------------

def resolve_instant(cf: dict, name: str, candidates: list[Candidate], end: date) -> Resolved:
    for c in candidates:
        f = xbrl.instant(xbrl.facts_for(cf, c.tag), end)
        if f is not None:
            note = f" ({c.note})" if c.note else ""
            return Resolved(name, c.sign * f.val, c.tag, f"as of {end}{note}")
    return Resolved(name, math.nan, None, f"no candidate tag has a value as of {end}")


def resolve_ltm(cf: dict, name: str, candidates: list[Candidate], end: date) -> Resolved:
    for c in candidates:
        r = xbrl.ltm(xbrl.facts_for(cf, c.tag), end)
        if r is not None:
            note = f" ({c.note})" if c.note else ""
            return Resolved(name, c.sign * r.value, c.tag, f"{r.periods}{note}")
    return Resolved(name, math.nan, None, f"no candidate tag gives an LTM value ending {end}")


def total_debt(long_term: Resolved, short_term: Resolved) -> float:
    """Long-term debt (required) + short-term borrowings (0 if none reported)."""
    if long_term.missing:
        return math.nan
    return long_term.value + (0.0 if short_term.missing else short_term.value)


# --------------------------------------------------------------------------
# Ratios (pure)
# --------------------------------------------------------------------------

def ebitda(operating_income: float, d_and_a: float) -> float:
    """Operating income + D&A. NaN if either input is missing."""
    if math.isnan(operating_income) or math.isnan(d_and_a):
        return math.nan
    return operating_income + d_and_a


def leverage(debt: float, ebitda_value: float) -> float:
    """Debt / EBITDA. NaN when EBITDA <= 0 (ratio not meaningful) or inputs missing."""
    if math.isnan(debt) or math.isnan(ebitda_value) or ebitda_value <= 0:
        return math.nan
    return debt / ebitda_value


def coverage(ebitda_value: float, interest: float) -> float:
    """EBITDA / interest expense. NaN when interest <= 0 or inputs missing."""
    if math.isnan(ebitda_value) or math.isnan(interest) or interest <= 0:
        return math.nan
    return ebitda_value / interest


# --------------------------------------------------------------------------
# Maturity schedule
# --------------------------------------------------------------------------

def fiscal_year_of(d: date, fye_month: int, fye_day: int) -> int:
    """Fiscal year label = calendar year in which that fiscal year ends."""
    return d.year if (d.month, d.day) <= (fye_month, fye_day) else d.year + 1


def last_completed_fy(end: date, fye_month: int, fye_day: int) -> int:
    """At a 10-K date the year just ended is complete; at a 10-Q date it's the prior one."""
    fy = fiscal_year_of(end, fye_month, fye_day)
    is_year_end = xbrl._near(end, date(fy, fye_month, fye_day))
    return fy if is_year_end else fy - 1


@dataclass
class MaturitySchedule:
    as_of: date | None
    buckets: dict[str, float] = field(default_factory=dict)   # "FY2027" -> USD
    tags_used: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(self.buckets.values()) if self.buckets else math.nan


def maturity_schedule(cf: dict, end: date, fye_month: int, fye_day: int) -> MaturitySchedule:
    """Scheduled principal repayments by fiscal year, from the newest filing that tags them.

    XBRL maturity buckets count from the last completed fiscal year:
    "next twelve months" / "remainder of fiscal year" = completed FY + 1,
    "year two" = completed FY + 2, ... "after year five" = completed FY + 6 and later.
    """
    by_tag = {k: xbrl.facts_for(cf, t) for k, t in MATURITY_TAGS.items()}
    dates = sorted({f.end for fs in by_tag.values() for f in fs if f.start is None and f.end <= end})
    if not dates:
        return MaturitySchedule(as_of=None)
    as_of = dates[-1]
    base = last_completed_fy(as_of, fye_month, fye_day)
    sched = MaturitySchedule(as_of=as_of)
    for key, facts in by_tag.items():
        f = xbrl.instant(facts, as_of)
        if f is None:
            continue
        if key == "after5":
            label = f"FY{base + 6}+"
        elif key == "remainder":
            label = f"FY{base + 1}"
        else:
            label = f"FY{base + key}"
        sched.buckets[label] = sched.buckets.get(label, 0.0) + f.val
        sched.tags_used.append(MATURITY_TAGS[key])
    sched.buckets = dict(sorted(sched.buckets.items(), key=lambda kv: (kv[0].endswith("+"), kv[0])))
    return sched


def near_term_share(sched: MaturitySchedule, end: date, fye_month: int, fye_day: int) -> float:
    """Share of scheduled principal due by the end of the NEXT fiscal year after the current one.

    At a 10-K date that is the next ~24 months; at a 10-Q date it is the rest of
    the current fiscal year plus the next full fiscal year.
    """
    if not sched.buckets or sched.as_of is None:
        return math.nan
    base = last_completed_fy(sched.as_of, fye_month, fye_day)
    near = sum(sched.buckets.get(f"FY{base + k}", 0.0) for k in (1, 2))
    return near / sched.total if sched.total > 0 else math.nan


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def rank_stretched(df: pd.DataFrame) -> pd.DataFrame:
    """Rank issuers from most to least stretched.

    Rank 1 = worst on each metric: highest leverage, lowest coverage, largest
    near-term maturity share. Composite = average of the available ranks.
    Missing metrics are not ranked (and are visible as NaN in the table).
    """
    out = df.copy()
    out["rank_leverage"] = out["debt_to_ebitda"].rank(ascending=False, method="min")
    out["rank_coverage"] = out["ebitda_to_interest"].rank(ascending=True, method="min")
    out["rank_near_term"] = out["near_term_pct"].rank(ascending=False, method="min")
    out["composite_rank"] = out[["rank_leverage", "rank_coverage", "rank_near_term"]].mean(axis=1)
    return out.sort_values("composite_rank", na_position="last").reset_index(drop=True)
