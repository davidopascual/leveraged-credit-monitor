"""Tests for issuer metrics: ratios, LTM construction, tag fallback, maturities, units.

All inputs are small hand-made dicts shaped like SEC companyfacts JSON.
"""

import math
from datetime import date

import pandas as pd
import pytest

from lcm import capstructure, xbrl
from lcm import fundamentals as fm


def cf(tags: dict[str, list[dict]]) -> dict:
    """Build a minimal companyfacts document: {tag: [ {start?, end, val, filed}, ... ]}."""
    return {
        "facts": {
            "us-gaap": {
                tag: {"units": {"USD": [{"form": "10-Q", "filed": "2026-11-01", **r} for r in rows]}}
                for tag, rows in tags.items()
            }
        }
    }


# ---- ratios ----------------------------------------------------------------

def test_ebitda_is_operating_income_plus_da():
    assert fm.ebitda(4811, 445) == 5256


def test_ebitda_missing_input_is_nan():
    assert math.isnan(fm.ebitda(math.nan, 445))


def test_leverage_and_coverage():
    assert fm.leverage(34_763, 5_256) == pytest.approx(6.614, abs=1e-3)
    assert fm.coverage(5_256, 2_008) == pytest.approx(2.618, abs=1e-3)


def test_ratios_not_meaningful_when_denominator_nonpositive():
    assert math.isnan(fm.leverage(1000, 0))
    assert math.isnan(fm.leverage(1000, -50))      # negative EBITDA -> n.m., not negative leverage
    assert math.isnan(fm.coverage(500, 0))


def test_total_debt_adds_short_term_only_if_reported():
    lt = fm.Resolved("long_term_debt", 100.0, "LongTermDebt")
    st = fm.Resolved("short_term_debt", 10.0, "ShortTermBorrowings")
    none = fm.Resolved("short_term_debt", math.nan, None)
    assert fm.total_debt(lt, st) == 110.0
    assert fm.total_debt(lt, none) == 100.0
    assert math.isnan(fm.total_debt(fm.Resolved("long_term_debt", math.nan, None), st))


# ---- XBRL: de-duplication and LTM ----------------------------------------

def test_latest_filed_value_wins_for_same_period():
    doc = cf({"OperatingIncomeLoss": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 100, "filed": "2026-02-01"},
        {"start": "2025-01-01", "end": "2025-12-31", "val": 90, "filed": "2027-02-01"},  # restated
    ]})
    facts = xbrl.facts_for(doc, "OperatingIncomeLoss")
    assert len(facts) == 1 and facts[0].val == 90


def test_ltm_uses_full_year_when_period_end_is_fiscal_year_end():
    doc = cf({"X": [{"start": "2025-10-01", "end": "2026-09-30", "val": 500}]})
    r = xbrl.ltm(xbrl.facts_for(doc, "X"), date(2026, 9, 30))
    assert r.value == 500 and r.method == "FY"


def test_ltm_from_10q_is_fy_plus_ytd_minus_prior_ytd():
    # FY2025 = 100; 9M 2026 = 80; 9M 2025 = 70  ->  LTM = 100 + 80 - 70 = 110
    doc = cf({"X": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 100},
        {"start": "2026-01-01", "end": "2026-09-30", "val": 80},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 70},
        {"start": "2026-07-01", "end": "2026-09-30", "val": 30},   # Q3 alone: must be ignored
    ]})
    r = xbrl.ltm(xbrl.facts_for(doc, "X"), date(2026, 9, 30))
    assert r.value == 110 and r.method == "FY + YTD - prior YTD"


def test_ltm_tolerates_52_53_week_quarter_ends():
    # Fiscal quarters ending on Saturdays: 2026-06-27 vs 2025-06-28.
    doc = cf({"X": [
        {"start": "2024-10-01", "end": "2025-09-30", "val": 400},
        {"start": "2025-10-01", "end": "2026-06-27", "val": 330},
        {"start": "2024-10-01", "end": "2025-06-28", "val": 290},
    ]})
    assert xbrl.ltm(xbrl.facts_for(doc, "X"), date(2026, 6, 27)).value == 440


def test_ltm_missing_piece_returns_none():
    doc = cf({"X": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 100},
        {"start": "2026-01-01", "end": "2026-09-30", "val": 80},
        # prior-year YTD missing
    ]})
    assert xbrl.ltm(xbrl.facts_for(doc, "X"), date(2026, 9, 30)) is None


# ---- tag fallback ----------------------------------------------------------

def test_resolve_falls_back_to_next_tag_and_records_it():
    doc = cf({"InterestExpenseNonoperating": [{"start": "2025-10-01", "end": "2026-09-30", "val": 7}]})
    r = fm.resolve_ltm(doc, "interest", fm.INTEREST_TAGS, date(2026, 9, 30))
    assert r.tag == "InterestExpenseNonoperating" and r.value == 7


def test_net_interest_tag_is_sign_flipped_and_labelled():
    doc = cf({"InterestIncomeExpenseNet": [{"start": "2025-10-01", "end": "2026-09-30", "val": -2008}]})
    r = fm.resolve_ltm(doc, "interest", fm.INTEREST_TAGS, date(2026, 9, 30))
    assert r.value == 2008 and "net of interest income" in r.detail


def test_all_candidates_missing_is_reported_not_filled():
    r = fm.resolve_ltm(cf({}), "d_and_a", fm.DA_TAGS, date(2026, 9, 30))
    assert r.missing and math.isnan(r.value)


def test_tag_with_value_for_wrong_period_is_skipped():
    # InterestExpense exists only for an older year; fall through to the next tag.
    doc = cf({
        "InterestExpense": [{"start": "2023-10-01", "end": "2024-09-30", "val": 1}],
        "InterestExpenseDebt": [{"start": "2025-10-01", "end": "2026-09-30", "val": 9}],
    })
    r = fm.resolve_ltm(doc, "interest", fm.INTEREST_TAGS, date(2026, 9, 30))
    assert r.tag == "InterestExpenseDebt"


# ---- maturities ------------------------------------------------------------

P = "LongTermDebtMaturitiesRepaymentsOfPrincipal"


def test_maturity_labels_from_10k_calendar_year():
    doc = cf({
        f"{P}InNextTwelveMonths": [{"end": "2025-12-31", "val": 10}],
        f"{P}InYearTwo": [{"end": "2025-12-31", "val": 20}],
        f"{P}AfterYearFive": [{"end": "2025-12-31", "val": 70}],
    })
    s = fm.maturity_schedule(doc, date(2025, 12, 31), 12, 31)
    assert s.buckets == {"FY2026": 10, "FY2027": 20, "FY2031+": 70}


def test_maturity_labels_from_10q_use_last_completed_year():
    # 10-Q at 2026-09-30: remainder of 2026, then year two = 2027.
    doc = cf({
        f"{P}RemainderOfFiscalYear": [{"end": "2026-09-30", "val": 5}],
        f"{P}InYearTwo": [{"end": "2026-09-30", "val": 15}],
    })
    s = fm.maturity_schedule(doc, date(2026, 9, 30), 12, 31)
    assert s.buckets == {"FY2026": 5, "FY2027": 15}


def test_maturity_labels_non_calendar_fiscal_year():
    # TDG-style September year end, 10-K at 2026-09-30: next twelve months = FY2027.
    doc = cf({f"{P}InNextTwelveMonths": [{"end": "2026-09-30", "val": 136}]})
    s = fm.maturity_schedule(doc, date(2026, 9, 30), 9, 30)
    assert s.buckets == {"FY2027": 136}


def test_near_term_share():
    doc = cf({
        f"{P}InNextTwelveMonths": [{"end": "2025-12-31", "val": 10}],
        f"{P}InYearTwo": [{"end": "2025-12-31", "val": 30}],
        f"{P}AfterYearFive": [{"end": "2025-12-31", "val": 60}],
    })
    s = fm.maturity_schedule(doc, date(2025, 12, 31), 12, 31)
    assert fm.near_term_share(s, date(2025, 12, 31), 12, 31) == pytest.approx(0.40)


def test_no_maturity_tags_gives_empty_schedule():
    s = fm.maturity_schedule(cf({}), date(2026, 9, 30), 12, 31)
    assert s.as_of is None and math.isnan(s.total)


# ---- ranking ---------------------------------------------------------------

def test_rank_most_stretched_first():
    df = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "debt_to_ebitda": [3.0, 7.0, 5.0],
        "ebitda_to_interest": [5.0, 1.5, 2.0],
        "near_term_pct": [10.0, 30.0, 5.0],
    })
    out = fm.rank_stretched(df)
    assert list(out["ticker"]) == ["B", "C", "A"]


def test_nonpositive_ebitda_ranks_worst_on_leverage():
    df = pd.DataFrame({
        "ticker": ["A", "B"],
        "debt_musd": [4906.0, 34763.0],
        "ltm_ebitda_musd": [-109.0, 5256.0],
        "debt_to_ebitda": [math.nan, 6.61],      # A is n.m. because EBITDA < 0
        "ebitda_to_interest": [-0.29, 2.62],
        "near_term_pct": [0.0, 1.6],
    })
    out = fm.rank_stretched(df).set_index("ticker")
    assert out.loc["A", "rank_leverage"] == 1
    assert math.isnan(out.loc["A", "debt_to_ebitda"])   # still displayed as n.m.


def test_rank_ignores_missing_metric():
    df = pd.DataFrame({
        "ticker": ["A", "B"],
        "debt_to_ebitda": [math.nan, 4.0],     # A: EBITDA <= 0 or missing
        "ebitda_to_interest": [1.0, 3.0],
        "near_term_pct": [10.0, 10.0],
    })
    out = fm.rank_stretched(df).set_index("ticker")
    assert math.isnan(out.loc["A", "rank_leverage"])
    assert out.loc["A", "composite_rank"] == pytest.approx(1.0)   # mean of coverage 1, near-term 1


# ---- units: Six Flags reports $ thousands, others $ millions --------------

def test_to_millions_handles_thousands_and_millions():
    assert capstructure.to_millions(1_473_703, "thousands") == pytest.approx(1_473.703)  # FUN term loan
    assert capstructure.to_millions(12_803, "millions") == pytest.approx(12_803)        # TDG term loans
    assert capstructure.to_millions(5.72, "billions") == pytest.approx(5_720)


def test_unknown_units_raise():
    with pytest.raises(ValueError):
        capstructure.to_millions(1, "lakhs")


def test_capital_structure_mixed_units_sum_in_millions(tmp_path):
    csv = tmp_path / "cs.csv"
    csv.write_text(
        "ticker,entity,instrument,class,priority,principal,units,maturity,form,accession,page,note\n"
        "FUN,Six Flags,Term loan,secured,1,1473703,thousands,2031,10-Q,x,15,\n"
        "FUN,Six Flags,2032 senior secured notes,secured,1,850000,thousands,2032,10-Q,x,15,\n"
        "HGV,HGV,Term loan B due 2031,secured,1,880,millions,2031,10-Q,y,20,\n"
        "HGV,HGV,Securitized Debt,nonrecourse,9,2346,millions,,10-Q,y,21,\n"
    )
    df = capstructure.load(csv)
    assert capstructure.recourse_principal_musd(df, "FUN") == pytest.approx(2_323.703)
    assert capstructure.recourse_principal_musd(df, "HGV") == pytest.approx(880)   # non-recourse excluded


def test_capital_structure_rejects_unknown_class(tmp_path):
    csv = tmp_path / "cs.csv"
    csv.write_text(
        "ticker,entity,instrument,class,priority,principal,units,maturity,form,accession,page,note\n"
        "X,X,Loan,mezzanine,1,1,millions,,10-K,x,1,\n"
    )
    with pytest.raises(ValueError):
        capstructure.load(csv)


# ---- long-term debt tagging quirk and debt-note maturity fallback -----------

def test_long_term_debt_adds_current_portion_when_filer_tags_noncurrent_only():
    # Six Flags pattern: LongTermDebt == LongTermDebtNoncurrent; current portion tagged separately.
    doc = cf({
        "LongTermDebt": [{"end": "2026-09-27", "val": 4891}],
        "LongTermDebtNoncurrent": [{"end": "2026-09-27", "val": 4891}],
        "LongTermDebtCurrent": [{"end": "2026-09-27", "val": 15}],
    })
    r = fm.resolve_long_term_debt(doc, date(2026, 9, 27))
    assert r.value == 4906 and r.tag == "LongTermDebt + LongTermDebtCurrent"


def test_long_term_debt_left_alone_when_it_already_includes_current():
    doc = cf({
        "LongTermDebt": [{"end": "2026-09-30", "val": 33890}],
        "LongTermDebtNoncurrent": [{"end": "2026-09-30", "val": 33743}],
        "LongTermDebtCurrent": [{"end": "2026-09-30", "val": 147}],
    })
    r = fm.resolve_long_term_debt(doc, date(2026, 9, 30))
    assert r.value == 33890 and r.tag == "LongTermDebt"


def test_schedule_from_debt_note_buckets_by_maturity_and_skips_nonrecourse():
    from lcm.issuers import schedule_from_debt_note
    rows = pd.DataFrame({
        "class": ["secured", "unsecured", "unsecured", "nonrecourse", "other"],
        "maturity": [2031, 2028, 2031, 2035, None],
        "principal_musd": [1473.703, 300.0, 800.0, 999.0, 8.0],
    })
    s = schedule_from_debt_note(rows, date(2026, 9, 27))
    assert s.buckets == pytest.approx({"FY2028": 300e6, "FY2031": 2273.703e6, "undated": 8e6})
