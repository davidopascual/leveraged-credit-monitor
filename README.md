# Leveraged Credit Monitor

A Python toolkit for monitoring US high-yield credit: spread regime monitoring, issuer
credit snapshots from SEC filings, and recovery waterfall scenarios.

> Work in progress. Milestone 1 (spread monitor) is complete. Full methodology,
> assumptions and limitations will be documented at Milestone 4.

## Setup

```bash
python3 -m venv ~/.venvs/credit-monitor
~/.venvs/credit-monitor/bin/pip install -e ".[dev]"
cp .env.example .env        # then add FRED_API_KEY and SEC_USER_AGENT
```

The virtual environment lives outside the project on purpose: on macOS with iCloud
Desktop sync, iCloud marks files inside the project as hidden, and Python 3.13 skips
hidden `.pth` files, which breaks `import lcm`. Keeping the venv in `~/.venvs` avoids it.

## Milestone 1: credit spread monitor

```bash
~/.venvs/credit-monitor/bin/python -m lcm spreads            # uses cached data in data/raw/ if present
~/.venvs/credit-monitor/bin/python -m lcm spreads --refresh  # re-download from FRED
~/.venvs/credit-monitor/bin/pytest
```

Data: ICE BofA option-adjusted spreads from FRED (IG `BAMLC0A0CM`, HY `BAMLH0A0HYM2`,
BB `BAMLH0A1HYBB`, B `BAMLH0A2HYB`, CCC & lower `BAMLH0A3HYC`). FRED serves only about
the last three years of these series; the CLI prints the exact date range it received.

Per series it reports the current level, 1-day / 5-day / 1-month change (bp),
percentile and z-score versus the trailing 252 observations, and flags the series when
|z| > 2 or the 5-day move exceeds a per-series threshold (see `config.yaml`). It also
tracks the CCC-minus-BB differential. Outputs go to `outputs/`.

## Milestone 2: issuer credit snapshots (SEC EDGAR)

```bash
~/.venvs/credit-monitor/bin/python -m lcm issuers            # all issuers in config.yaml
~/.venvs/credit-monitor/bin/python -m lcm issuers --only TDG
```

Data: SEC XBRL `companyfacts` and `submissions` APIs (data.sec.gov) plus the filing
documents themselves. Requests send a User-Agent with a name and email taken from the
`SEC_USER_AGENT` environment variable and are throttled to 5 requests/second (SEC limit: 10).
Everything is cached in `data/raw/sec/`.

### Definitions
- **Period:** the newest balance sheet available (latest 10-Q or 10-K, whichever is newer).
- **Total debt:** carrying amount of long-term debt including current maturities, plus
  short-term borrowings when reported separately (e.g. receivables securitization).
  Book value, net of unamortized discounts and issuance costs.
- **EBITDA (LTM):** GAAP operating income + depreciation & amortization over the last twelve
  months. Unadjusted: no add-backs. This is *not* the company's "Adjusted EBITDA" or the
  credit-agreement definition, which are usually higher.
- **LTM** = last fiscal year + current year-to-date − prior year-to-date (or the fiscal year
  itself when the newest filing is a 10-K).
- **Interest (LTM):** interest expense; if a company only tags a net figure (net of interest
  income) it is used and labelled as such in the tag audit.
- **Near-term maturities:** share of scheduled principal due by the end of the next fiscal
  year after the current one.
- **Ranking:** rank 1 = most stretched on each of leverage (highest), coverage (lowest) and
  near-term maturities (highest); composite = average rank. Debt/EBITDA is shown as n/a
  when EBITDA <= 0 but that issuer ranks *first* (worst) on leverage.
- **Supplementary (not ranked):** EBITDA before goodwill and intangible impairments, shown
  next to the headline figure because non-cash write-downs can push GAAP operating income
  negative.
- **Maturities:** from XBRL maturity tags; if a filer does not tag them, from the
  hand-entered debt note (bullet at each instrument's maturity year, amortization ignored),
  and the output says which source was used.

Every field's XBRL tag (or MISSING) is printed and saved to `outputs/xbrl_tag_audit.csv`.
XBRL total debt is reconciled against the gross principal entered by hand from each debt
note in `data/inputs/capital_structure.csv` (with filing and page for every row).

### Issuers and ratings
Ratings are taken only from the companies' own filings or releases, never from memory.
"Not in filing" means the latest 10-K and 10-Q do not disclose ratings; to be added from
Bloomberg (RATC).

| Ticker | Company | Latest data used | Ratings |
|---|---|---|---|
| TDG | TransDigm Group | 10-K FY ended 2026-09-30 (0001260221-26-000077) | Not in filing |
| FUN | Six Flags Entertainment | 10-Q quarter ended 2026-09-27 (0001999001-26-000107) | Not in filing |

### Issuer-specific notes and simplifications
- **TDG:** notes are secured (rank equally with the term loans) or senior *subordinated*
  (10-K p.28). Interest is tagged only as "interest expense, net". The XBRL maturity schedule
  covers long-term debt only; the $875M receivables securitization and $344M finance leases
  are not in it.
- **FUN:** debt tables are in **$ thousands** (others use $ millions); amounts are converted
  in `capstructure.to_millions` and tested. The filer tags `LongTermDebt` with the
  non-current amount only, so the $15.0M current portion is added back (rule in
  `fundamentals.resolve_long_term_debt`). LTM operating income is negative because of
  $717M of goodwill and trade-name impairments (10-Q p.4), so headline EBITDA is negative and
  leverage is n/a. No XBRL maturity schedule: maturities come from the debt note (10-Q p.15);
  the term loan's $15.0M/yr amortization is ignored. **Simplification:** the Partnership Park,
  Mexican and former La Ronde subsidiaries are not borrowers, guarantors or collateral under
  the credit agreement and secured notes (10-Q p.16); the model treats Six Flags as a single
  borrowing group and does not separate those subsidiaries' value.
