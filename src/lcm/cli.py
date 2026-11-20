"""Command line entry point: python -m lcm <command>."""

from __future__ import annotations

import argparse

import pandas as pd

from lcm import charts, fred, spreads
from lcm.config import OUTPUT_DIR, RAW_DIR, get_secret, load_config


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lcm", description="Leveraged Credit Monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("spreads", help="HY/IG spread monitor from FRED")
    p.add_argument("--refresh", action="store_true", help="re-download from FRED instead of cache")
    args = parser.parse_args(argv)

    if args.command == "spreads":
        return run_spreads(args.refresh)
    return 1
