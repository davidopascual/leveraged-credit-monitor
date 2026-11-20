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

    snaps = [
        issuers.build_snapshot(i, ua, cache, refresh, cap[cap["ticker"] == i["ticker"]], mat[mat["ticker"] == i["ticker"]])
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
    table = issuers.fm.rank_stretched(pd.DataFrame(rows))

    print("\nISSUER CREDIT SNAPSHOT ($ millions; EBITDA = operating income + D&A, LTM, unadjusted)")
    show = ["composite_rank", "ticker", "period_end", "debt_musd", "ltm_ebitda_musd", "ltm_interest_musd",
            "debt_to_ebitda", "ebitda_to_interest", "near_term_pct", "rank_leverage", "rank_coverage",
            "rank_near_term"]
    print(table[show].to_string(index=False, na_rep="n/a", formatters={
        "composite_rank": "{:.1f}".format, "debt_musd": "{:,.0f}".format, "ltm_ebitda_musd": "{:,.0f}".format,
        "ltm_interest_musd": "{:,.0f}".format, "debt_to_ebitda": "{:.2f}x".format,
        "ebitda_to_interest": "{:.2f}x".format, "near_term_pct": "{:.1f}%".format,
        "rank_leverage": "{:.0f}".format, "rank_coverage": "{:.0f}".format, "rank_near_term": "{:.0f}".format}))
    print("near_term_pct = share of scheduled principal due by the end of the next fiscal year after the current one")

    print("\nSUPPLEMENTARY (not used in ranking): EBITDA before goodwill & intangible impairments")
    print(table[["ticker", "ltm_impairments_musd", "ebitda_pre_impairment_musd", "debt_to_ebitda_pre_impairment",
                 "maturity_source"]].to_string(index=False, na_rep="n/a", formatters={
        "ltm_impairments_musd": "{:,.0f}".format, "ebitda_pre_impairment_musd": "{:,.0f}".format,
        "debt_to_ebitda_pre_impairment": "{:.2f}x".format}))

    print("\nDEBT RECONCILIATION: XBRL total debt (carrying) vs debt-note gross principal (data/inputs)")
    for r in rows:
        diff = r["debt_musd"] - r["debt_note_principal_musd"]
        print(f"  {r['ticker']}: XBRL {r['debt_musd']:,.0f} vs debt note {r['debt_note_principal_musd']:,.0f} "
              f"(diff {diff:+,.0f}; unamortized discounts and issuance costs explain small gaps)")

    OUTPUT_DIR.mkdir(exist_ok=True)
    table.to_csv(OUTPUT_DIR / "issuer_snapshot.csv", index=False)
    audit.to_csv(OUTPUT_DIR / "xbrl_tag_audit.csv", index=False)
    saved = [OUTPUT_DIR / "issuer_snapshot.csv", OUTPUT_DIR / "xbrl_tag_audit.csv"]
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
