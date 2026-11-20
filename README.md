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
- **Near-term maturities:** share of scheduled principal due in the rest of the current
  fiscal year plus the next two fiscal years (~24 months from a 10-K date, ~27 months from a
  Q3 10-Q). XBRL maturity buckets are counted from the fiscal year that contains the
  balance-sheet date (in a 10-Q, "next twelve months" is the year after the one in progress;
  checked against Bausch Health's printed table, 10-Q p.23).
- **Ranking:** rank 1 = most stretched on each of leverage (highest), coverage (lowest) and
  near-term maturities (highest); composite = average rank. Debt/EBITDA is shown as n/a
  when EBITDA <= 0 but that issuer ranks *first* (worst) on leverage.
- **Supplementary (not ranked):** EBITDA excluding goodwill/intangible impairments (added
  back) and gains on the sale of a business (removed), shown next to the headline figure
  because one-off items can distort GAAP operating income in either direction.
- **Maturities:** in order of preference: (1) XBRL maturity tags dated within the last
  year (older schedules are stale and ignored); (2) the maturity table printed in the filing,
  entered by hand in `data/inputs/maturity_tables.csv`; (3) the debt note, as a bullet at each
  instrument's maturity year (amortization ignored). The output says which source was used.

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
| HGV | Hilton Grand Vacations | 10-Q quarter ended 2026-09-30 (0001674168-26-000108) | Not in filing |
| LUMN | Lumen Technologies | 10-Q quarter ended 2026-09-30 (0000018926-26-000073) | Moody's CFR **B2**, Fitch IDR **B** (10-Q p.58 footnote, upgrades Feb 2026). S&P issuer credit rating **B-** affirmed (Lumen news release, 2026-02-23, p.2). Issue ratings as of the 10-Q filing date (2026-11-04, p.58): Lumen secured B3/Caa1 · B+ · BB, unsecured Caa1 · B · BB; Level 3 Financing secured Ba3 · B+ · BB, unsecured B3 · B- · B-; Qwest unsecured Caa1 · B · BB (Moody's · S&P · Fitch) |

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
- **HGV:** only corporate (recourse) debt is in total debt, ratios and the waterfall. The
  $2,923M of non-recourse timeshare debt (warehouse facilities and securitizations, payable
  solely from pledged timeshare receivables; 10-Q p.21) is shown as a separate line. This is
  consistent with EBITDA: interest on that debt sits inside the "Financing" operating
  expense, so it is already deducted from operating income. HGV's income statement has no
  operating income subtotal, so operating income = total revenues − total operating expenses
  (`Revenues − CostsAndExpenses`, an opt-in fallback for HGV only; interest is below that
  line on p.2). Non-recourse debt is not tagged in current XBRL, so its total comes from the
  debt note. **Assumption:** the 6.625% senior notes due 2032 are "guaranteed on a senior
  secured basis" (p.21); the filings do not say whether they share collateral pari passu with
  the credit facility. The model assumes they do (to be checked against the indenture).
- **LUMN:** the Mass Markets Fiber-to-the-Home business sold to AT&T on Feb 2, 2026 was
  *not* treated as a discontinued operation (10-Q p.14), so the LTM window (Oct 2025 - Sep
  2026) still contains its results up to the sale date. The filings do not disclose that
  business's EBITDA, so it cannot be removed; the CLI prints a caveat. Headline EBITDA also
  includes the $569M net gain on the sale (inside operating income, 10-Q p.5); the
  supplementary column removes it. Debt is the consolidated total ($13,193M carrying, tag
  `DebtAndCapitalLeaseObligations`). **Entity-level data is not available:** Qwest
  Corporation deregistered (Form 15-12G, 2026-06-12; last 10-Q for Q1 2026) and Level 3
  Parent, LLC has filed no 10-Q since Q3 2025, so neither has financials as of 2026-09-30.
  The debt sits at Lumen (parent), Level 3 Financing and Qwest; the snapshot is consolidated.
