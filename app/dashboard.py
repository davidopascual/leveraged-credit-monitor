"""Leveraged Credit Monitor - single-page Streamlit dashboard.

Shows what the CLI commands produce; it does not recompute anything differently.
Run the pipeline first, then the app:

    python -m lcm spreads && python -m lcm issuers && python -m lcm recovery
    streamlit run app/dashboard.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from lcm import fred, spreads
from lcm.config import OUTPUT_DIR, RAW_DIR, load_config

st.set_page_config(page_title="Leveraged Credit Monitor", layout="wide")
CFG = load_config()


def need(*files: str) -> bool:
    """True if all output files exist; otherwise show which command creates them."""
    missing = [f for f in files if not (OUTPUT_DIR / f).exists()]
    if missing:
        st.warning(f"Missing {', '.join(missing)}. Run the CLI first (see the commands at the top of this file).")
    return not missing


def stamp(*files: str) -> str:
    t = max((OUTPUT_DIR / f).stat().st_mtime for f in files)
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def fmt_x(v: float) -> str:
    return "n/a" if pd.isna(v) else f"{v:.2f}x"


st.title("Leveraged Credit Monitor")
st.caption("US high-yield spreads (FRED), issuer credit snapshots (SEC EDGAR) and recovery scenarios. "
           "Public data only; scenario recoveries are estimates, not forecasts.")

# ---------------------------------------------------------------------------
# 1. Spread monitor
# ---------------------------------------------------------------------------
st.header("1. Credit spread monitor")
if need("spread_summary.csv"):
    summary = pd.read_csv(OUTPUT_DIR / "spread_summary.csv")
    st.caption(f"ICE BofA option-adjusted spreads via FRED; table generated {stamp('spread_summary.csv')}. "
               f"Percentile and z-score vs the trailing {CFG['spreads']['windows']['y1']} observations; "
               f"flag = |z| > {CFG['spreads']['z_threshold']:g} or 5-day move above the per-series threshold.")
    st.dataframe(summary.style.format({
        "level_bp": "{:.0f}", "chg_1d_bp": "{:+.0f}", "chg_5d_bp": "{:+.0f}", "chg_1m_bp": "{:+.0f}",
        "pctile_1y": "{:.0f}", "z_1y": "{:+.2f}"}, na_rep="n/a"), hide_index=True, width="stretch")

    cache = RAW_DIR / "fred"
    try:
        series = {name: fred.load_series(sid, None, CFG["fred"]["base_url"], cache).values
                  for name, sid in CFG["fred"]["series"].items()}
        levels = pd.DataFrame({k: v.map(spreads.to_bp) for k, v in series.items()})
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("OAS levels (bp)")
            st.line_chart(levels, height=320)
        with c2:
            st.subheader("CCC − BB differential (bp)")
            diff = spreads.spread_difference(series["CCC"], series["BB"]).map(spreads.to_bp)
            st.line_chart(pd.DataFrame({"CCC − BB": diff}), height=320)
        first, last = levels.dropna().index.min().date(), levels.dropna().index.max().date()
        st.caption(f"History available from FRED: {first} to {last} (FRED serves about three years of these series).")
    except RuntimeError as e:
        st.info(f"Cached FRED data not found ({e}). Run `python -m lcm spreads` once with FRED_API_KEY set.")

# ---------------------------------------------------------------------------
# 2. Issuer snapshots
# ---------------------------------------------------------------------------
st.header("2. Issuer credit snapshots")
if need("issuer_snapshot.csv"):
    snap = pd.read_csv(OUTPUT_DIR / "issuer_snapshot.csv")
    footnoted = {i["ticker"] for i in CFG["issuers"] if i.get("footnotes")}
    snap["Issuer"] = [f"{t}*" if t in footnoted else t for t in snap["ticker"]]
    st.caption(f"LTM figures, $ millions; generated {stamp('issuer_snapshot.csv')}. "
               "Headline = adjusted EBITDA (operating income + D&A + goodwill/intangible impairments − gains "
               "(+ losses) on business or disposal-group sales). Rank 1 = most stretched.")
    head = snap.sort_values("composite_rank")[[
        "composite_rank", "Issuer", "period_end", "debt_musd", "ebitda_adj_musd", "ltm_interest_musd",
        "debt_to_ebitda_adj", "ebitda_to_interest_adj", "near_term_pct",
        "rank_leverage", "rank_coverage", "rank_near_term"]]
    st.subheader("Headline ranking: adjusted EBITDA")
    st.dataframe(head.style.format({
        "composite_rank": "{:.1f}", "debt_musd": "{:,.0f}", "ebitda_adj_musd": "{:,.0f}",
        "ltm_interest_musd": "{:,.0f}", "debt_to_ebitda_adj": fmt_x, "ebitda_to_interest_adj": fmt_x,
        "near_term_pct": "{:.1f}%", "rank_leverage": "{:.0f}", "rank_coverage": "{:.0f}",
        "rank_near_term": "{:.0f}"}, na_rep="n/a"), hide_index=True, width="stretch")
    st.caption("The composite averages ranks, so it ignores the size of differences: read the individual metrics too.")

    st.subheader("Same issuers on reported (unadjusted) EBITDA")
    rep = snap.sort_values("reported_composite_rank")[[
        "reported_composite_rank", "Issuer", "ebitda_reported_musd", "ltm_impairments_musd",
        "ltm_gain_on_sale_musd", "debt_to_ebitda_reported", "ebitda_to_interest_reported",
        "operating_income_basis", "maturity_source"]]
    st.dataframe(rep.style.format({
        "reported_composite_rank": "{:.1f}", "ebitda_reported_musd": "{:,.0f}", "ltm_impairments_musd": "{:,.0f}",
        "ltm_gain_on_sale_musd": "{:,.0f}", "debt_to_ebitda_reported": fmt_x,
        "ebitda_to_interest_reported": fmt_x}, na_rep="n/a"), hide_index=True, width="stretch")

    notes = [f"**{i['ticker']}\\***: {fn}" for i in CFG["issuers"] for fn in i.get("footnotes", [])]
    notes += [f"**{i['ticker']}**: {c}" for i in CFG["issuers"] for c in i.get("caveats", [])]
    with st.expander("Footnotes and caveats", expanded=True):
        for n in notes:
            st.markdown(f"- {n}")
        st.markdown("- **HGV**: $2,923M of non-recourse timeshare debt is excluded from total debt, ratios and the waterfall.")

    c1, c2 = st.columns([3, 2])
    with c1:
        p = OUTPUT_DIR / "leverage_adjusted_vs_reported.png"
        if p.exists():
            st.image(str(p), caption="Adjusted (headline) vs reported leverage and coverage")
    with c2:
        tick = st.selectbox("Maturity wall", list(snap.sort_values("composite_rank")["ticker"]))
        p = OUTPUT_DIR / f"maturity_wall_{tick}.png"
        if p.exists():
            st.image(str(p))

# ---------------------------------------------------------------------------
# 3. Recovery scenarios
# ---------------------------------------------------------------------------
st.header("3. Recovery scenarios")
if need("coverage_multiples.csv", "recovery_by_group.csv", "recovery_scenarios.csv"):
    r = CFG["recovery"]
    st.caption(f"Generated {stamp('recovery_by_group.csv')}. Default-scenario EBITDA = LTM adjusted EBITDA × "
               f"(1 − {r['default_ebitda_haircut']:.0%}); admin & priority claims = {r['admin_pct']:.0%} of EV; "
               f"claims = principal. Multiples ({', '.join(f'{m:g}x' for m in r['multiples'])}) are illustrative "
               "and applied uniformly across industries.")
    cov = pd.read_csv(OUTPUT_DIR / "coverage_multiples.csv")
    st.subheader("Coverage multiples (no multiple assumed)")
    st.caption("Cumulative principal through each class ÷ LTM adjusted EBITDA: the EV/EBITDA multiple at which "
               "that class and everything senior to it is fully covered. Second column: same after the haircut "
               "and admin claims.")
    st.dataframe(cov.sort_values(["ticker", "display_order"])[[
        "ticker", "group", "principal_musd", "cumulative_musd", "ebitda_musd", "covered_at_x",
        "covered_at_x_in_scenarios", "ebitda_basis"]].style.format({
            "principal_musd": "{:,.0f}", "cumulative_musd": "{:,.0f}", "ebitda_musd": "{:,.0f}",
            "covered_at_x": "{:.2f}x", "covered_at_x_in_scenarios": "{:.2f}x"}),
        hide_index=True, width="stretch")

    grp = pd.read_csv(OUTPUT_DIR / "recovery_by_group.csv")
    rec_cols = [c for c in grp.columns if c.startswith("recovery_")]
    st.subheader("Scenario recoveries by seniority (% of principal)")
    tick = st.selectbox("Issuer", list(grp["ticker"].unique()), key="rec")
    g = grp[grp.ticker == tick].sort_values("display_order")
    st.dataframe(g[["group", "principal_musd", *rec_cols]].style.format(
        {"principal_musd": "{:,.0f}", **{c: "{:.0f}%" for c in rec_cols}}), hide_index=True, width="stretch")
    p = OUTPUT_DIR / f"recovery_{tick}.png"
    if p.exists():
        st.image(str(p))
    if tick == "LUMN":
        st.info("Consolidated waterfall: entity-level structure would change recoveries, in different directions "
                "for different notes (see README).")

    pv = OUTPUT_DIR / "price_vs_recovery.csv"
    if pv.exists():
        st.subheader("Price vs scenario recovery")
        st.dataframe(pd.read_csv(pv), hide_index=True, width="stretch")
    else:
        st.caption("No prices file (data/inputs/prices.csv, git-ignored licensed data), so the price comparison is skipped.")

st.divider()
st.caption("Sources: FRED (ICE BofA OAS), SEC EDGAR XBRL and filings, company news release (Lumen, 2026-02-23). "
           "See README for methodology, assumptions and limitations.")
