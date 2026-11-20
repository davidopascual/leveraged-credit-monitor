"""Build one credit snapshot per issuer from EDGAR data (I/O lives here, math in fundamentals)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from lcm import edgar
from lcm import fundamentals as fm


@dataclass
class Snapshot:
    ticker: str
    period_end: date
    source_filing: str                     # e.g. "10-K 0001260221-26-000077"
    fields: dict[str, fm.Resolved]
    debt_usd: float
    ebitda_usd: float
    schedule: fm.MaturitySchedule
    near_term_pct: float
    maturity_source: str


def latest_period(subs: dict) -> tuple[date, dict]:
    """Newest balance-sheet date among the latest 10-K and 10-Q, and that filing."""
    candidates = [f for form in ("10-K", "10-Q") for f in edgar.latest_filings(subs, form, 1)]
    best = max(candidates, key=lambda f: f["report_date"])
    return date.fromisoformat(best["report_date"]), best


def schedule_from_printed_table(rows: pd.DataFrame, as_of: date) -> fm.MaturitySchedule:
    """Maturity schedule from a maturity table printed in the filing (hand-entered)."""
    from lcm.capstructure import to_millions
    sched = fm.MaturitySchedule(as_of=as_of, tags_used=["printed maturity table (data/inputs/maturity_tables.csv)"])
    last_year = max(int(y) for y in rows["year"] if str(y) != "after")
    for _, r in rows.iterrows():
        label = f"FY{last_year + 1}+" if str(r["year"]) == "after" else f"FY{int(r['year'])}"
        sched.buckets[label] = sched.buckets.get(label, 0.0) + to_millions(r["principal"], r["units"]) * 1e6
    sched.buckets = dict(sorted(sched.buckets.items(), key=lambda kv: (kv[0].endswith("+"), kv[0])))
    return sched


def schedule_from_debt_note(rows: pd.DataFrame, as_of: date) -> fm.MaturitySchedule:
    """Fallback maturity schedule from hand-entered debt-note rows (bullet at maturity year).

    Used only when the filer tags no XBRL maturity schedule. Ignores scheduled
    amortization; rows without a maturity year go in an "undated" bucket.
    Non-recourse debt is excluded.
    """
    sched = fm.MaturitySchedule(as_of=as_of, tags_used=["debt note (data/inputs/capital_structure.csv)"])
    for _, r in rows[rows["class"] != "nonrecourse"].iterrows():
        label = f"FY{int(r['maturity'])}" if pd.notna(r["maturity"]) else "undated"
        sched.buckets[label] = sched.buckets.get(label, 0.0) + r["principal_musd"] * 1e6
    sched.buckets = dict(sorted(sched.buckets.items(), key=lambda kv: (kv[0] == "undated", kv[0])))
    return sched


def build_snapshot(
    issuer: dict, user_agent: str, cache_dir: Path, refresh: bool = False,
    debt_note_rows: pd.DataFrame | None = None,
    maturity_table_rows: pd.DataFrame | None = None,
) -> Snapshot:
    cik = issuer["cik"]
    fye_month, fye_day = (int(x) for x in issuer["fye"].split("-"))
    subs = edgar.submissions(cik, user_agent, cache_dir, refresh)
    cf = edgar.company_facts(cik, user_agent, cache_dir, refresh)
    end, filing = latest_period(subs)

    fields = {
        "long_term_debt": fm.resolve_long_term_debt(cf, end),
        "short_term_debt": fm.resolve_instant(cf, "short_term_debt", fm.SHORT_TERM_DEBT_TAGS, end),
        "operating_income": fm.resolve_operating_income(cf, end, issuer.get("operating_income_fallback")),
        "d_and_a": fm.resolve_ltm(cf, "d_and_a", fm.DA_TAGS, end),
        "interest": fm.resolve_ltm(cf, "interest", fm.INTEREST_TAGS, end),
        "impairments": fm.resolve_ltm(cf, "impairments", fm.IMPAIRMENT_TAGS, end),
    }
    debt = fm.total_debt(fields["long_term_debt"], fields["short_term_debt"])
    ebitda = fm.ebitda(fields["operating_income"].value, fields["d_and_a"].value)
    sched = fm.maturity_schedule(cf, end, fye_month, fye_day)
    source = "XBRL"
    if not sched.buckets and maturity_table_rows is not None and not maturity_table_rows.empty:
        sched = schedule_from_printed_table(maturity_table_rows, end)
        source = "printed maturity table (XBRL maturity tags missing)"
    elif not sched.buckets and debt_note_rows is not None and not debt_note_rows.empty:
        sched = schedule_from_debt_note(debt_note_rows, end)
        source = "debt note bullets (no XBRL tags or printed table)"
    return Snapshot(
        ticker=issuer["ticker"],
        period_end=end,
        source_filing=f"{filing['form']} {filing['accession']}",
        fields=fields,
        debt_usd=debt,
        ebitda_usd=ebitda,
        schedule=sched,
        near_term_pct=fm.near_term_share(sched, end, fye_month, fye_day),
        maturity_source=source,
    )


def metrics_row(s: Snapshot, capstructure_musd: float | None) -> dict[str, object]:
    m = 1e6
    interest = s.fields["interest"].value
    row = {
        "ticker": s.ticker,
        "period_end": s.period_end,
        "filing": s.source_filing,
        "debt_musd": s.debt_usd / m,
        "ltm_ebitda_musd": s.ebitda_usd / m,
        "ltm_interest_musd": interest / m,
        "debt_to_ebitda": fm.leverage(s.debt_usd, s.ebitda_usd),
        "ebitda_to_interest": fm.coverage(s.ebitda_usd, interest),
        "near_term_pct": s.near_term_pct * 100 if not math.isnan(s.near_term_pct) else math.nan,
        "maturities_as_of": s.schedule.as_of,
        "maturity_source": s.maturity_source,
        "debt_note_principal_musd": capstructure_musd if capstructure_musd is not None else math.nan,
    }
    # Supplementary, not used in ranking: EBITDA before goodwill/intangible impairments.
    imp = s.fields["impairments"]
    imp_usd = 0.0 if imp.missing else imp.value
    pre = s.ebitda_usd + imp_usd
    row["ltm_impairments_musd"] = imp_usd / m
    row["ebitda_pre_impairment_musd"] = pre / m
    row["debt_to_ebitda_pre_impairment"] = fm.leverage(s.debt_usd, pre)
    return row


def audit_rows(s: Snapshot) -> list[dict[str, object]]:
    rows = [
        {
            "ticker": s.ticker,
            "field": r.field,
            "tag": r.tag or ("none reported (treated as 0)" if r.field in ("short_term_debt", "impairments") else "MISSING"),
            "value_musd": r.value / 1e6,
            "detail": r.detail,
        }
        for r in s.fields.values()
    ]
    rows.append({
        "ticker": s.ticker,
        "field": "maturities",
        "tag": ", ".join(t.replace("LongTermDebtMaturitiesRepaymentsOfPrincipal", "…") for t in s.schedule.tags_used) or "MISSING",
        "value_musd": s.schedule.total / 1e6,
        "detail": (f"as of {s.schedule.as_of}; source: {s.maturity_source}" if s.schedule.as_of
                   else "no maturity tags found and no debt-note rows"),
    })
    return rows


def maturity_frame(s: Snapshot) -> pd.DataFrame:
    return pd.DataFrame(
        {"bucket": list(s.schedule.buckets), "principal_musd": [v / 1e6 for v in s.schedule.buckets.values()]}
    )
