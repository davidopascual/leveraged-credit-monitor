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
from datetime import date, timedelta

import pandas as pd

from lcm import xbrl


@dataclass(frozen=True)
class Candidate:
    tag: str
    sign: float = 1.0       # -1 for "net" tags reported as negative expense
    note: str = ""
    includes_short_term: bool = False   # debt tags that already include short-term borrowings


DEBT_TAGS = [
    Candidate("LongTermDebt"),
    Candidate("DebtLongtermAndShorttermCombinedAmount", includes_short_term=True),
    Candidate("DebtAndCapitalLeaseObligations", includes_short_term=True),
    Candidate("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"),
    Candidate("DebtInstrumentCarryingAmount", note="instrument carrying amount"),
]
SHORT_TERM_INCLUSIVE_TAGS = {c.tag for c in DEBT_TAGS if c.includes_short_term}
# By the US-GAAP taxonomy definition, LongTermDebt excludes short-term borrowings,
# so adding these does not double count.
SHORT_TERM_DEBT_TAGS = [
    Candidate("ShortTermBorrowings"),
    Candidate("ShortTermBankLoansAndNotesPayable"),
]
# Supplementary only (shown, never used in the headline EBITDA or the ranking):
# EBITDA + impairments - gains (or + losses) on sales/write-downs of businesses or disposal groups.
IMPAIRMENT_TAGS = [
    Candidate("GoodwillAndIntangibleAssetImpairment"),
    Candidate("GoodwillImpairmentLoss", note="goodwill only"),
]
GAIN_ON_BUSINESS_SALE_TAGS = [
    Candidate("GainLossOnSaleOfBusiness"),
    Candidate("DisposalGroupNotDiscontinuedOperationGainLossOnDisposal"),
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
MAX_SCHEDULE_AGE_DAYS = 370   # a maturity schedule older than ~1 year is treated as missing
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
    partial: bool = False   # True: some data exists (e.g. current YTD) but not enough for the period

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


def resolve_operating_income(cf: dict, end: date, fallback: str | None = None) -> Resolved:
    """Operating income (LTM). Optional, per-issuer fallback when no subtotal is reported.

    fallback="revenues_minus_costs_and_expenses": LTM Revenues - LTM CostsAndExpenses.
    Only valid for filers whose CostsAndExpenses line is total OPERATING expenses
    (interest and other non-operating items below it); enable it per issuer in
    config.yaml after checking the printed income statement.
    """
    r = resolve_ltm(cf, "operating_income", OPERATING_INCOME_TAGS, end)
    if not r.missing or fallback is None:
        return r
    if fallback != "revenues_minus_costs_and_expenses":
        raise ValueError(f"unknown operating income fallback {fallback!r}")
    rev = resolve_ltm(cf, "revenues", [Candidate("Revenues")], end)
    costs = resolve_ltm(cf, "costs", [Candidate("CostsAndExpenses")], end)
    if rev.missing or costs.missing:
        return r
    return Resolved(
        "operating_income", rev.value - costs.value, "Revenues - CostsAndExpenses",
        f"{rev.detail}; derived (issuer has no operating income subtotal)",
    )


def resolve_long_term_debt(cf: dict, end: date) -> Resolved:
    """Long-term debt including current maturities.

    Some filers tag LongTermDebt with the NON-current amount (identical to
    LongTermDebtNoncurrent) even though the taxonomy defines it to include the
    current portion. When that happens and a current portion is reported, add it.
    """
    r = resolve_instant(cf, "long_term_debt", DEBT_TAGS, end)
    if r.tag != "LongTermDebt":
        return r
    noncurrent = xbrl.instant(xbrl.facts_for(cf, "LongTermDebtNoncurrent"), end)
    current = xbrl.instant(xbrl.facts_for(cf, "LongTermDebtCurrent"), end)
    if noncurrent is not None and current is not None and noncurrent.val == r.value and current.val > 0:
        return Resolved(
            "long_term_debt", r.value + current.val, "LongTermDebt + LongTermDebtCurrent",
            f"as of {end} (filer's LongTermDebt equals LongTermDebtNoncurrent, so current portion added)",
        )
    return r


def resolve_ltm(cf: dict, name: str, candidates: list[Candidate], end: date) -> Resolved:
    """First candidate tag that yields a complete LTM value.

    If no tag gives a complete LTM but some tag HAS a value for a period ending at
    `end` (e.g. year-to-date), the result is MISSING with partial=True. That is not
    "none reported": the item exists, but prior-period facts needed for LTM are not
    tagged, so it must not be treated as zero.
    """
    partial: list[str] = []
    for c in candidates:
        facts = xbrl.facts_for(cf, c.tag)
        r = xbrl.ltm(facts, end)
        if r is not None:
            note = f" ({c.note})" if c.note else ""
            return Resolved(name, c.sign * r.value, c.tag, f"{r.periods}{note}")
        current = [f for f in facts if f.start is not None and f.end == end and f.val != 0]
        if current:
            f = max(current, key=lambda f: f.days)
            partial.append(f"{c.tag} {f.start}..{f.end} = {c.sign * f.val / 1e6:,.0f}M")
    if partial:
        return Resolved(
            name, math.nan, None,
            "PARTIAL: " + "; ".join(partial) + " found, but prior-period facts are not tagged, so LTM is not computable",
            partial=True,
        )
    return Resolved(name, math.nan, None, f"no candidate tag gives an LTM value ending {end}")


def total_debt(long_term: Resolved, short_term: Resolved) -> float:
    """Long-term debt (required) + short-term borrowings (0 if none reported).

    If the debt tag used already includes short-term borrowings, nothing is added.
    """
    if long_term.missing:
        return math.nan
    if short_term.missing or long_term.tag in SHORT_TERM_INCLUSIVE_TAGS:
        return long_term.value
    return long_term.value + short_term.value


def ebitda_excl_one_offs(ebitda_value: float, impairments: float, gains_on_sale: float) -> float:
    """Supplementary EBITDA: add back impairments, remove gains on sale of a business."""
    if math.isnan(ebitda_value) or math.isnan(impairments) or math.isnan(gains_on_sale):
        return math.nan
    return ebitda_value + impairments - gains_on_sale


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

    Buckets count from the fiscal year that CONTAINS the schedule date (base):
    - 10-K at year end: base = the year just ended; "next twelve months" = base + 1.
    - 10-Q mid-year: base = the year in progress; "remainder of fiscal year" = base,
      "next twelve months" = base + 1 (e.g. Bausch Health 10-Q p.23: remainder of
      2026 = $15M, 2027 = $701M).
    "year N" = base + N; "after year five" = base + 6 and later.
    """
    by_tag = {k: xbrl.facts_for(cf, t) for k, t in MATURITY_TAGS.items()}
    # Only schedules from the last year count; an older one (filer stopped tagging) is stale.
    oldest_ok = end - timedelta(days=MAX_SCHEDULE_AGE_DAYS)
    dates = sorted({f.end for fs in by_tag.values() for f in fs
                    if f.start is None and oldest_ok <= f.end <= end})
    if not dates:
        return MaturitySchedule(as_of=None)
    as_of = dates[-1]
    base = fiscal_year_of(as_of, fye_month, fye_day)
    sched = MaturitySchedule(as_of=as_of)
    for key, facts in by_tag.items():
        f = xbrl.instant(facts, as_of)
        if f is None:
            continue
        if key == "after5":
            label = f"FY{base + 6}+"
        elif key == "remainder":
            label = f"FY{base}"
        else:
            label = f"FY{base + key}"
        sched.buckets[label] = sched.buckets.get(label, 0.0) + f.val
        sched.tags_used.append(MATURITY_TAGS[key])
    sched.buckets = dict(sorted(sched.buckets.items(), key=lambda kv: (kv[0].endswith("+"), kv[0])))
    return sched


def near_term_share(sched: MaturitySchedule, end: date, fye_month: int, fye_day: int) -> float:
    """Share of scheduled principal due in the rest of the current fiscal year plus the next two.

    At a 10-K date that is the next ~24 months; from a Q3 10-Q, about 27 months.
    """
    if not sched.buckets or sched.as_of is None:
        return math.nan
    base = fiscal_year_of(sched.as_of, fye_month, fye_day)
    near = sum(sched.buckets.get(f"FY{base + k}", 0.0) for k in (0, 1, 2))
    return near / sched.total if sched.total > 0 else math.nan


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def rank_stretched(df: pd.DataFrame) -> pd.DataFrame:
    """Rank issuers from most to least stretched.

    Rank 1 = worst on each metric: highest leverage, lowest coverage, largest
    near-term maturity share. Composite = average of the available ranks.
    Debt/EBITDA is not meaningful when EBITDA <= 0, but such an issuer is the
    MOST stretched on leverage, so it ranks first there. Truly missing data
    (NaN EBITDA or debt) is not ranked and stays visible as NaN.
    """
    out = df.copy()
    lev_key = out["debt_to_ebitda"].copy()
    if "ltm_ebitda_musd" in out:
        nonpositive = (out["ltm_ebitda_musd"] <= 0) & out["debt_musd"].notna()
        lev_key[nonpositive] = math.inf
    out["rank_leverage"] = lev_key.rank(ascending=False, method="min")
    out["rank_coverage"] = out["ebitda_to_interest"].rank(ascending=True, method="min")
    out["rank_near_term"] = out["near_term_pct"].rank(ascending=False, method="min")
    out["composite_rank"] = out[["rank_leverage", "rank_coverage", "rank_near_term"]].mean(axis=1)
    return out.sort_values("composite_rank", na_position="last").reset_index(drop=True)
