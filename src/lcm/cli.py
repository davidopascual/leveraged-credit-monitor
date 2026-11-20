"""Command line entry point: python -m lcm <command>."""

from __future__ import annotations

import argparse

import pandas as pd

from lcm import capstructure, charts, fred, issuers, spreads
from lcm.config import OUTPUT_DIR, RAW_DIR, ROOT, get_secret, load_config


def run_spreads(refresh: bool) -> int:
    cfg = load_config()
    fcfg, scfg = cfg["fred"], cfg["spreads"]
    api_key = get_secret("FRED_API_KEY")
    cache = RAW_DIR / "fred"

    loaded: dict[str, fred.FredSeries] = {
        name: fred.load_series(sid, api_key, fcfg["base_url"], cache, refresh)
        for name, sid in fcfg["series"].items()
    }

    # 1. What data do we actually have?
    coverage = pd.DataFrame(
        [
            {
                "series": name,
                "fred_id": fs.series_id,
                "first": fs.values.index[0].date(),
                "last": fs.values.index[-1].date(),
                "obs": len(fs.values),
                "missing_dropped": fs.n_missing,
                "fetched_on": fs.fetched_on,
            }
            for name, fs in loaded.items()
        ]
    )
    print("\nDATA COVERAGE (as returned by FRED)")
    print(coverage.to_string(index=False))

    # 2. Daily summary table
    series = {name: fs.values for name, fs in loaded.items()}
    rows = [
        spreads.summarize(name, s, scfg["windows"], scfg["z_threshold"],
                          scfg["move_5d_threshold_bp"][name])
        for name, s in series.items()
    ]
    diff = spreads.spread_difference(series["CCC"], series["BB"])
    rows.append(
        spreads.summarize("CCC-BB", diff, scfg["windows"], scfg["z_threshold"],
                          scfg["move_5d_threshold_bp"]["CCC"])
    )
    table = pd.DataFrame(rows)

    print("\nDAILY SPREAD SUMMARY (bp; pctile and z vs trailing "
          f"{scfg['windows']['y1']} observations)")
    print(
        table.to_string(
            index=False,
            na_rep="n/a",
            formatters={
                "level_bp": "{:.0f}".format,
                "chg_1d_bp": "{:+.0f}".format,
                "chg_5d_bp": "{:+.0f}".format,
                "chg_1m_bp": "{:+.0f}".format,
                "pctile_1y": "{:.0f}".format,
                "z_1y": "{:+.2f}".format,
            },
        )
    )
    flagged = table[table["flag"] != ""]
    print(f"\nFLAGS (|z| > {scfg['z_threshold']:g} or |5d move| > per-series threshold): "
          + ("none" if flagged.empty else ", ".join(f"{r.series} [{r.flag}]"
                                                       for r in flagged.itertuples())))

    # 3. Outputs
    OUTPUT_DIR.mkdir(exist_ok=True)
    table.to_csv(OUTPUT_DIR / "spread_summary.csv", index=False)
    p1 = charts.plot_oas_levels(series, OUTPUT_DIR / "oas_levels.png")
    p2 = charts.plot_ccc_minus_bb(diff, scfg["windows"]["y1"], OUTPUT_DIR / "ccc_minus_bb.png")
    print(f"\nSaved: {OUTPUT_DIR / 'spread_summary.csv'}\n       {p1}\n       {p2}")
    return 0


def run_issuers(refresh: bool, only: list[str] | None) -> int:
    cfg = load_config()
    ua = get_secret("SEC_USER_AGENT") or ""
    cache = RAW_DIR / "sec"
    cap = capstructure.load(ROOT / "data" / "inputs" / "capital_structure.csv")
    mat = pd.read_csv(ROOT / "data" / "inputs" / "maturity_tables.csv", comment="#", dtype={"year": str})
    selected = [i for i in cfg["issuers"] if not only or i["ticker"] in only]

    overrides = issuers.load_overrides(ROOT / "data" / "inputs" / "manual_overrides.csv")
    snaps = [
        issuers.build_snapshot(i, ua, cache, refresh, cap[cap["ticker"] == i["ticker"]],
                               mat[mat["ticker"] == i["ticker"]], overrides.get(i["ticker"]))
        for i in selected
    ]

    audit = pd.DataFrame([r for s in snaps for r in issuers.audit_rows(s)])
    print("\nXBRL TAG AUDIT (which tag supplied each field; MISSING = no candidate tag had a value)")
    print(audit.to_string(index=False, formatters={"value_musd": "{:,.0f}".format}, na_rep="n/a",
                          max_colwidth=90))

    rows = []
    for s in snaps:
        in_csv = (cap["ticker"] == s.ticker).any()
        rows.append(issuers.metrics_row(s, capstructure.recourse_principal_musd(cap, s.ticker) if in_csv else None))
    table = pd.DataFrame(rows)
    table = issuers.fm.rank_stretched(table, "debt_to_ebitda_reported", "ebitda_to_interest_reported",
                                      "ebitda_reported_musd", prefix="reported_", sort=False)
    table = issuers.fm.rank_stretched(table, "debt_to_ebitda_adj", "ebitda_to_interest_adj", "ebitda_adj_musd")

    footnoted = {i["ticker"] for i in selected if i.get("footnotes")}
    table["ticker_label"] = [f"{t}*" if t in footnoted else t for t in table["ticker"]]
    x = "{:.2f}x".format
    m0 = "{:,.0f}".format
    r0 = "{:.0f}".format
    print("\nISSUER CREDIT SNAPSHOT - HEADLINE: ADJUSTED EBITDA ($ millions, LTM)")
    print("Adjusted EBITDA = operating income + D&A + goodwill/intangible impairments - gains (+ losses) on")
    print("business or disposal-group sales. Not the company's 'Adjusted EBITDA' (no other add-backs).")
    print(table[["composite_rank", "ticker_label", "period_end", "debt_musd", "ebitda_adj_musd", "ltm_interest_musd",
                 "debt_to_ebitda_adj", "ebitda_to_interest_adj", "near_term_pct", "rank_leverage", "rank_coverage",
                 "rank_near_term"]].rename(columns={"ticker_label": "ticker"}).to_string(
        index=False, na_rep="n/a", formatters={
            "composite_rank": "{:.1f}".format, "debt_musd": m0, "ebitda_adj_musd": m0, "ltm_interest_musd": m0,
            "debt_to_ebitda_adj": x, "ebitda_to_interest_adj": x, "near_term_pct": "{:.1f}%".format,
            "rank_leverage": r0, "rank_coverage": r0, "rank_near_term": r0}))
    print("near_term_pct = share of scheduled principal due in the rest of this fiscal year plus the next two")

    print("\nSAME ISSUERS ON REPORTED (UNADJUSTED) EBITDA = operating income + D&A, for comparison")
    print(table[["reported_composite_rank", "ticker_label", "ebitda_reported_musd", "ltm_impairments_musd",
                 "ltm_gain_on_sale_musd", "debt_to_ebitda_reported", "ebitda_to_interest_reported",
                 "operating_income_basis", "maturity_source"]].rename(columns={"ticker_label": "ticker"}).to_string(
        index=False, na_rep="n/a", formatters={
            "reported_composite_rank": "{:.1f}".format, "ebitda_reported_musd": m0, "ltm_impairments_musd": m0,
            "ltm_gain_on_sale_musd": m0, "debt_to_ebitda_reported": x, "ebitda_to_interest_reported": x}))
    print("Debt/EBITDA shows n/a when EBITDA <= 0; that issuer still ranks worst on leverage.")

    print("\nFOOTNOTES")
    for i in selected:
        for fn in i.get("footnotes", []):
            print(f"  * {i['ticker']}: {fn}")
    ovr = [(t, f) for t, d in overrides.items() for f in d if t in set(table["ticker"])]
    for t, f in ovr:
        print(f"  {t}: {f} is a manual value from the printed filing (data/inputs/manual_overrides.csv)")

    nonrec = cap[cap["class"] == "nonrecourse"].groupby("ticker")["principal_musd"].sum()
    for t, v in nonrec.items():
        if t in set(table["ticker"]):
            print(f"\nNON-RECOURSE DEBT (separate line, excluded from total debt, ratios and waterfall): {t} {v:,.0f}")

    caveats = [(i["ticker"], c) for i in selected for c in i.get("caveats", [])]
    if caveats:
        print("\nCAVEATS")
        for tk, c in caveats:
            print(f"  {tk}: {c}")

    print("\nDEBT RECONCILIATION: XBRL total debt (carrying) vs debt-note gross principal (data/inputs)")
    for r in rows:
        diff = r["debt_musd"] - r["debt_note_principal_musd"]
        print(f"  {r['ticker']}: XBRL {r['debt_musd']:,.0f} vs debt note {r['debt_note_principal_musd']:,.0f} "
              f"(diff {diff:+,.0f}; carrying value = principal net of unamortized premiums, discounts and issuance costs)")

    OUTPUT_DIR.mkdir(exist_ok=True)
    table.to_csv(OUTPUT_DIR / "issuer_snapshot.csv", index=False)
    audit.to_csv(OUTPUT_DIR / "xbrl_tag_audit.csv", index=False)
    saved = [OUTPUT_DIR / "issuer_snapshot.csv", OUTPUT_DIR / "xbrl_tag_audit.csv",
             charts.plot_leverage_comparison(table, OUTPUT_DIR / "leverage_adjusted_vs_reported.png")]
    for s in snaps:
        mf = issuers.maturity_frame(s)
        mf.to_csv(OUTPUT_DIR / f"maturities_{s.ticker}.csv", index=False)
        if not mf.empty:
            saved.append(charts.plot_maturity_wall(s.ticker, mf, str(s.schedule.as_of), s.source_filing,
                                                   OUTPUT_DIR / f"maturity_wall_{s.ticker}.png"))
        else:
            print(f"  {s.ticker}: no maturity schedule tagged in XBRL; no chart")
    print("\nSaved:\n  " + "\n  ".join(str(p) for p in saved))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lcm", description="Leveraged Credit Monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("spreads", help="HY/IG spread monitor from FRED")
    p.add_argument("--refresh", action="store_true", help="re-download from FRED instead of cache")
    p2 = sub.add_parser("issuers", help="issuer credit snapshots from SEC XBRL")
    p2.add_argument("--refresh", action="store_true", help="re-download from SEC instead of cache")
    p2.add_argument("--only", nargs="*", help="tickers to include (default: all in config)")
    args = parser.parse_args(argv)

    if args.command == "spreads":
        return run_spreads(args.refresh)
    if args.command == "issuers":
        return run_issuers(args.refresh, args.only)
    return 1
