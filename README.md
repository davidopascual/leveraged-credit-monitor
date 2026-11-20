# Leveraged Credit Monitor

A Python toolkit for looking at US leveraged credit the way a credit desk would, using
only public data:

1. **Spread monitor:** daily ICE BofA option-adjusted spreads (IG, high yield, BB, B, CCC)
   from FRED, with changes, percentiles, z-scores, flags and the CCC−BB differential.
2. **Issuer credit snapshots:** leverage, interest coverage and maturity walls for five
   high-yield issuers, computed from SEC EDGAR XBRL data and reconciled to the debt notes.
3. **Recovery waterfall:** how enterprise value would be shared across each issuer's
   capital structure under stated scenarios, including a legal-entity model for Bausch
   Health.
4. **Dashboard:** a single Streamlit page showing all three.

Every number comes from FRED, SEC EDGAR (XBRL data and the filings themselves), one
company news release (cited), or a hand-entered input file that records the filing and
page for each number. Nothing is invented; where data is missing the output says so.

## Quick start

```bash
python3 -m venv ~/.venvs/credit-monitor
~/.venvs/credit-monitor/bin/pip install -e ".[dev,dashboard]"
cp .env.example .env        # then add FRED_API_KEY and SEC_USER_AGENT ("Your Name you@example.com")

PY=~/.venvs/credit-monitor/bin/python
$PY -m lcm spreads          # Milestone 1: spread table + charts
$PY -m lcm issuers          # Milestone 2: issuer snapshot, tag audit, maturity walls
$PY -m lcm recovery         # Milestone 3: coverage multiples + recovery scenarios
~/.venvs/credit-monitor/bin/streamlit run app/dashboard.py     # Milestone 4: dashboard
~/.venvs/credit-monitor/bin/pytest                             # tests
```

The first run downloads and caches raw data in `data/raw/`; later runs work offline
(`--refresh` re-downloads). Outputs (tables and charts) go to `outputs/`. Both folders are
git-ignored and can be recreated from the code.

The virtual environment lives outside the project on purpose: on macOS with iCloud
Desktop sync, iCloud marks files inside a synced folder as hidden, and Python 3.13 skips
hidden `.pth` files, which breaks `import lcm`. Keeping the venv in `~/.venvs` avoids it.

## Data sources

| Source | What | Access |
|---|---|---|
| FRED (St. Louis Fed) | ICE BofA US OAS: `BAMLC0A0CM`, `BAMLH0A0HYM2`, `BAMLH0A1HYBB`, `BAMLH0A2HYB`, `BAMLH0A3HYC` | API key in `FRED_API_KEY` |
| SEC EDGAR | XBRL `companyfacts` and `submissions` APIs; 10-K, 10-Q and 8-K documents | User-Agent from `SEC_USER_AGENT`, 5 requests/s |
| Company release | Lumen news release, 2026-02-23 (S&P issuer rating) | Lumen investor relations site |
| Hand-entered inputs | `data/inputs/capital_structure.csv`, `maturity_tables.csv`, `manual_overrides.csv` | Each row cites form, accession number and page |
| Optional, not included | `data/inputs/prices.csv` (Bloomberg loan/bond prices) | Git-ignored licensed data |

## Repository layout

```
src/lcm/      fred.py, edgar.py (clients + caching) · spreads.py, fundamentals.py, waterfall.py
              (pure math) · xbrl.py, filing_text.py (parsing) · issuers.py, recovery.py,
              capstructure.py (assembly) · charts.py · cli.py
app/          dashboard.py (Streamlit)
data/inputs/  hand-entered capital structures, maturity tables, manual overrides (all cited)
tests/        test_spreads.py, test_fundamentals.py, test_waterfall.py, test_dashboard.py
docs/         issuer_review_2026-11-19.txt (evidence trail for the issuer selection)
config.yaml   every threshold, window and scenario assumption
```

## Dashboard (Milestone 4)

`app/dashboard.py` is one page with three sections: the spread table with interactive
OAS and CCC−BB charts (read from the FRED cache), the issuer rankings on adjusted and
reported EBITDA with footnotes, caveats and maturity walls, and the coverage multiples and
recovery scenarios per issuer. It displays the tables the CLI writes to `outputs/`, so the
dashboard always matches the printed numbers; run the three CLI commands first.

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
- **Reported EBITDA (LTM):** GAAP operating income + depreciation & amortization over the
  last twelve months.
- **Adjusted EBITDA (LTM) - the headline:** reported EBITDA + goodwill/intangible impairments
  − gains (+ losses) on the sale or write-down of a business or disposal group. Only these
  two items, each read from a tagged line or a cited manual value. It is *not* the
  company's "Adjusted EBITDA" or the credit-agreement definition (no add-backs for
  restructuring, stock compensation, synergies, etc.), which are usually higher.
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
- **Which ranking is the headline, and why:** the ranking on **adjusted** EBITDA. Goodwill
  impairments are non-cash and do not change a company's ability to pay interest, and a gain
  on selling a business is a one-time event, not recurring earnings. On reported EBITDA
  those items dominate: Six Flags' EBITDA is negative because of $717M of impairments,
  Bausch Health's leverage looks like 8.6x instead of 5.2x because of a $1,571M write-down,
  and Lumen's looks *lower* (4.7x vs 5.9x) because of a $569M gain. The ranking on reported
  EBITDA is printed beside the headline, and `outputs/leverage_adjusted_vs_reported.png`
  shows both.
- **Partial data is never zero:** if an item has a current year-to-date value but the
  prior-period facts needed for LTM are not tagged, the field is MISSING (partial) and
  anything depending on it is n/a. Where the printed filing supplies the missing periods,
  the value is entered in **`data/inputs/manual_overrides.csv`** with the filing and page
  for each number (currently BHC impairments $1,571M and Six Flags' $41.0M loss on a
  disposal group). No hand-entered number lives in code or `config.yaml`.
- **Maturities:** in order of preference: (1) XBRL maturity tags dated within the last
  year (older schedules are stale and ignored); (2) the maturity table printed in the filing,
  entered by hand in `data/inputs/maturity_tables.csv`; (3) the debt note, as a bullet at each
  instrument's maturity year (amortization ignored). The output says which source was used.

### Results (as printed by `lcm issuers`, data as of the periods in the issuer table)
Headline ranking on adjusted EBITDA, most stretched first:

| Rank | Ticker | Composite | Debt / adj. EBITDA | Adj. EBITDA / interest | Near-term maturities | Rank: leverage / coverage / near-term |
|---|---|---|---|---|---|---|
| 1 | FUN | 1.7 | 7.56x | 1.73x | 6.1% | 1 / 1 / 3 |
| 2 | BHC | 2.3 | 5.20x | 2.49x | 22.2% | 4 / 2 / 1 |
| 3 | LUMN | 3.3 | 5.85x | 2.52x | 1.8% | 3 / 3 / 4 |
| 4 | TDG | 3.7 | 6.61x | 2.62x | 1.6% | 2 / 4 / 5 |
| 5 | HGV* | 4.0 | 4.12x | 3.99x | 9.3% | 5 / 5 / 2 |

On reported (unadjusted) EBITDA the order is FUN 1.7 (EBITDA −$109M, leverage n/a), BHC 1.7
(8.61x), TDG 3.7 (6.61x), LUMN 4.0 (4.67x), HGV 4.0 (4.12x).

How to read the composite: it averages three *ranks*, so it ignores how big the
differences are. Lumen ranks above TransDigm on the composite even though TransDigm is
more levered (6.61x vs 5.85x), because Lumen is slightly worse on coverage (2.52x vs
2.62x) and near-term maturities (1.8% vs 1.6%), differences that are too small to mean
much. Read the individual metrics, not just the composite.

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
| BHC | Bausch Health Companies | 10-Q quarter ended 2026-09-30 (0000885590-26-000123) | As of 2026-10-28 (10-Q p.71). BHC: Moody's Caa2 corporate / Caa1 secured / Ca unsecured; S&P B- / B- / CCC+. Bausch + Lomb: Moody's B1 corporate; S&P B / B secured; Fitch B / BB secured |
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
  reported leverage is n/a (adjusted: 7.56x). No XBRL maturity schedule: maturities come from the debt note (10-Q p.15);
  the term loan's $15.0M/yr amortization is ignored. **Simplification:** the Partnership Park,
  Mexican and former La Ronde subsidiaries are not borrowers, guarantors or collateral under
  the credit agreement and secured notes (10-Q p.16); the model treats Six Flags as a single
  borrowing group and does not separate those subsidiaries' value.
- **HGV (marked `*` in the output):** only corporate (recourse) debt is in total debt,
  ratios and the waterfall. The $2,923M of non-recourse timeshare debt (warehouse facilities
  and securitizations, payable solely from pledged timeshare receivables; 10-Q p.21) is shown
  as a separate line. **Operating income is derived**, not reported: HGV's income statement
  has no operating income subtotal, so operating income = total revenues − total operating
  expenses (`Revenues − CostsAndExpenses`, an opt-in fallback for HGV only; corporate
  interest expense sits below that line, 10-Q p.2). **Not comparable with the other
  issuers:** total operating expenses include the "Financing" line, which "represents
  consumer financing interest expense related to our debt securitized by gross timeshare
  financing receivables … and Timeshare Facility" (10-K p.48); that interest was $117M of
  the $215M financing expense in FY2025 (10-K p.55). HGV's EBITDA is therefore struck
  *after* securitization interest, and its coverage ratio uses only corporate interest, so
  its EBITDA, leverage and coverage are on a different basis from the other four.
  Non-recourse debt is not tagged in current XBRL, so its total comes from the debt note.
  **2032 secured notes - verified pari passu:** the 8-K announcing the notes (filed
  2024-01-17, accession 0001193125-24-009277, Item 1.01) states the term loans "rank pari
  passu in right of payment and pari passu in right of security with … the Notes" and that
  the notes "are secured on a first-priority basis by substantially all assets … which assets
  also secure the Company's senior secured credit facilities". The indenture (Exhibit 4.1,
  Section 1.01) defines the "Equal Priority Intercreditor Agreement" as "the First Lien
  Intercreditor Agreement dated January 17, 2024" between the credit agreement collateral
  agent and the notes' collateral agent.
- **LUMN:** the Mass Markets Fiber-to-the-Home business sold to AT&T on Feb 2, 2026 was
  *not* treated as a discontinued operation (10-Q p.14), so the LTM window (Oct 2025 - Sep
  2026) still contains its results up to the sale date. The filings do not disclose that
  business's EBITDA, so it cannot be removed; the CLI prints a caveat. Because LTM EBITDA
  still includes earnings from a business Lumen no longer owns, while debt is measured after
  the sale proceeds repaid debt, **the leverage shown likely understates Lumen's leverage on
  its current business.** Reported EBITDA also includes the $569M net gain on the sale
  (inside operating income, 10-Q p.5); adjusted EBITDA removes it. Debt is the consolidated
  total ($13,193M carrying, tag `DebtAndCapitalLeaseObligations`). **Entity-level data is
  not available:** Qwest Corporation deregistered (Form 15-12G, 2026-06-12; last 10-Q for
  Q1 2026) and Level 3 Parent, LLC has filed no 10-Q since Q3 2025, so neither has
  financials as of 2026-09-30. The debt sits at Lumen (parent), Level 3 Financing and Qwest;
  the snapshot and the Milestone 3 waterfall are consolidated (see the limitation there).
- **BHC:** consolidated, including Bausch + Lomb (consolidated by BHC). Debt sits in three
  pools (10-Q p.16-22): Bausch + Lomb's own loans and notes, secured only by B+L assets;
  126NumberCo's term loan B and 10% 2032 notes, secured by the B+L shares it holds plus a
  pari passu first-lien guarantee from BHC's guarantors; and BHC's existing first-lien,
  second-lien and unsecured notes, which 126NumberCo does not guarantee. The legal entity of
  each instrument is recorded in `data/inputs/capital_structure.csv` for the Milestone 3
  entity-level waterfall. LTM goodwill impairment is $1,571M (FY2025 $145M, 10-K p.F-6, plus
  9M 2026 $1,426M, 10-Q p.2); XBRL tags only the 2026 amount, so it is a manual override.
  Carrying debt ($20,637M) exceeds principal ($20,168M) because of unamortized premiums from
  exchanges.

## Milestone 3: recovery waterfall

```bash
~/.venvs/credit-monitor/bin/python -m lcm recovery            # all issuers
~/.venvs/credit-monitor/bin/python -m lcm recovery --only BHC
```

Outputs: `outputs/coverage_multiples.csv`, `recovery_scenarios.csv` (EV, admin,
distributable value, residual equity per scenario), `recovery_by_group.csv`,
`recovery_by_instrument.csv`, and a chart per issuer (`recovery_<TICKER>.png`). The
waterfall itself is a pure function in `src/lcm/waterfall.py`.

### First table: coverage multiples (no multiple assumed)
For each debt class, in seniority order: **cumulative principal through that class ÷ LTM
adjusted EBITDA**. This is the EV/EBITDA multiple at which the class, and everything senior
to it, is fully covered. It needs no valuation assumption, so it is printed first. A second
column restates it on the scenario basis (after the 20% EBITDA haircut and 5% admin
claims); a class recovers 100% in any scenario whose multiple is at or above that number.
For BHC, Bausch + Lomb classes are measured against B+L's EBITDA, and 126NumberCo / BHC
classes against BHC-ex-B+L EBITDA with no credit for B+L equity.

| Issuer | Class | Covered at (x LTM adj. EBITDA) | On scenario basis |
|---|---|---|---|
| TDG | Securitization / first lien / unsecured / subordinated | 0.17x / 5.01x / 5.08x / 6.66x | 0.22x / 6.60x / 6.68x / 8.76x |
| FUN | First lien / unsecured | 3.58x / 7.59x | 4.71x / 9.98x |
| HGV | First lien / unsecured | 2.97x / 4.17x | 3.91x / 5.49x |
| LUMN | First lien / unsecured | 3.03x / 5.92x | 3.98x / 7.78x |
| BHC | B+L secured / B+L other | 6.42x / 6.44x | 8.45x / 8.47x |
| BHC | BHC first lien (incl. 126NumberCo) / second lien / unsecured | 3.35x / 3.46x / 4.75x | 4.41x / 4.55x / 6.25x |

### Second: scenario recoveries
The 4.0x / 5.5x / 7.0x scenarios follow, as described below.

### How it works
1. **Default-scenario EBITDA** = LTM *adjusted* EBITDA (the Milestone 2 headline) × (1 − haircut).
2. **Enterprise value** = default EBITDA × distressed EV/EBITDA multiple (floored at zero).
3. **Administrative and other priority claims** (professional fees, priority tax and
   employee claims) take a fixed percentage of EV off the top.
4. **Distributable value** pays claims in order of priority. Claims with the same priority
   share pro rata (pari passu). Anything left is residual equity.

### Assumptions (all in `config.yaml` → `recovery`)
| Assumption | Value | Why / comment |
|---|---|---|
| EV/EBITDA multiples | 4.0x, 5.5x, 7.0x | Low / base / high distressed valuations. |
| Default EBITDA haircut | 20% below LTM adjusted EBITDA | Companies usually default after earnings fall; 20% is a round-number assumption, not an estimate for any issuer. |
| Admin & priority claims | 5% of EV | Simplified proxy for bankruptcy costs and claims ahead of funded debt. |
| Claim size | Principal only, from the debt note | No accrued/default interest, make-whole premiums or fees. |
| Revolvers | Drawn amount at the period end | In practice revolvers are often fully drawn before default; undrawn commitments are ignored. |
| Undersecured creditors | No deficiency claim | A secured lender's shortfall does not join the unsecured class (it would, in practice, dilute unsecured recoveries). |
| Leases, government advances, "other" debt | General unsecured claims | Simplification; a lessor in practice recovers via the leased asset. |
| Non-recourse debt (HGV) | Excluded | Paid only from its own pledged receivables. |
| Base case for price comparison | 5.5x | |

### Seniority used per issuer
- **TDG:** receivables securitization (paid first, from pledged receivables) → term loans and
  secured notes (pari passu, 10-K p.28) → leases and government advances (unsecured) →
  senior *subordinated* notes (contractually subordinated to senior debt, 10-K p.28).
- **FUN:** term loan and 2032 secured notes (same collateral, 10-Q p.16) → unsecured notes.
  Simplification: the Partnership Park, Mexican and former La Ronde subsidiaries are outside
  the collateral and guarantor group (10-Q p.16) but are not valued separately.
- **HGV:** credit facility (term loans A/B, drawn revolver) and 6.625% 2032 notes, **pari
  passu first lien, verified** (8-K 2024-01-17 and indenture, see Milestone 2 notes) →
  unsecured 2029/2031 notes and other debt. Corporate EV is EBITDA-based; HGV's EBITDA
  includes financing profit on receivables that also back the non-recourse debt, so its
  corporate EV may be overstated (not adjusted).
- **LUMN - consolidated waterfall (limitation):** Level 3 Financing's term loan B and first
  lien notes → all unsecured notes (Lumen parent, Level 3, Qwest, Qwest Capital Funding) and
  leases, pro rata. Entity-level financials are not available (Qwest deregistered in June
  2026; Level 3 Parent has filed no 10-Q since Q3 2025), so value cannot be allocated by legal
  entity. **An entity-level structure would change recoveries, and not in one direction:**
  the parent's unsecured notes sit structurally behind Level 3's debt (they would recover
  *less* than shown), while Qwest's notes have first claim on Qwest's own assets (they
  could recover *more*). The consolidated result is therefore not a consistent over- or
  under-estimate across notes. In addition, Lumen's LTM EBITDA still contains the fiber
  business sold to AT&T up to Feb 2, 2026, which likely overstates the EBITDA of the business
  that remains (understates leverage) and so may overstate recoveries.
- **BHC - by legal entity:** three boxes (see `recovery.bhc_entity_waterfall`):
  1. **Bausch + Lomb:** B+L's value (its own LTM adjusted EBITDA, $787M, from B+L's 10-Q
     0001860742-26-000111) pays B+L's own term loan, revolver and secured notes, then its
     "other" debt. B+L equity is split by shareholding: 211,963,893 shares pledged by
     126NumberCo (10-Q p.18), 98,485,750 other BHC-held shares (310,449,643 total BHC-held,
     B+L 10-Q p.6, less the pledged shares), and the public, using 357,497,374 shares
     outstanding (B+L 10-Q cover, XBRL). The public's share never reaches BHC creditors.
  2. **126NumberCo:** the value of the pledged B+L shares pays its 2030 term loan B and 10%
     2032 notes pro rata. Any shortfall is claimed under their pari passu first-lien
     guarantee from BHC (10-Q p.18, p.21); any surplus goes up to BHC.
  3. **BHC RemainCo:** BHC's own value (BHC adjusted EBITDA − B+L adjusted EBITDA) plus
     126NumberCo surplus plus the other BHC-held B+L equity pays: first lien (existing 4.875%
     and 11% notes + 126NumberCo guarantee claims), then the 14% second lien notes, then
     unsecured notes.
  Simplifications: the guarantee claim is the 126NumberCo shortfall (not the full face
  amount); intercompany claims and the guarantees from "NumberCo Note Guarantors" outside
  the existing guarantor group are ignored; the 98.5M non-pledged B+L shares are treated as
  unencumbered BHC value; B+L EBITDA and BHC-ex-B+L EBITDA use the same multiple.

### Optional price comparison
`data/inputs/prices.csv` is **git-ignored and never committed**: the prices are licensed
Bloomberg data. Only its format is described here. If the file exists (columns
`ticker,instrument,price,price_date,source`; instrument names exactly as in
`capital_structure.csv`; price per 100 face), the command
compares each price with its scenario recoveries. *Cushion* = base-case recovery − price,
in points; within each issuer the tranche with the largest cushion looks cheapest
relative to the others in the same capital structure. If the file is absent, the
comparison is skipped and the output says so.

### Caveats on the assumptions
- **The multiples are illustrative.** 4.0x / 5.5x / 7.0x are applied uniformly to
  companies in very different industries (aerospace parts, theme parks, timeshares,
  telecom, pharma) that would trade at different distressed multiples. For Bausch Health
  the same multiple is applied to Bausch + Lomb and to the rest of BHC, which would likely
  be valued differently. The coverage-multiple table above avoids this assumption.
- **The 20% EBITDA haircut and 5% admin claims are assumptions**, not estimates for any
  issuer. Both are in `config.yaml`.
- **Deficiency claims are left out.** Where the first-lien collateral is substantially all
  assets (TDG, HGV, Lumen's Level 3 debt, BHC's guarantors), a secured lender's shortfall
  would mostly come from the same pool anyway, so the effect is small. It matters mainly at
  **Six Flags**, where the Partnership Park, Mexican and former La Ronde subsidiaries sit
  outside the collateral: their value would be shared by secured lenders' deficiency claims
  and unsecured noteholders, so the model likely overstates the unsecured recovery there.

### What the results are (and are not)
Recoveries are **scenario estimates, not forecasts**: they show how value would be shared
under stated multiples and haircuts, using public filings only. They depend most on the
multiple and the EBITDA haircut, then on seniority and (for BHC) on legal-entity structure.

## Assumptions (summary)
All are in `config.yaml` unless noted, and each is discussed in its section above.

| Area | Assumption |
|---|---|
| Spreads | Windows in trading observations: 1d=1, 5d=5, 1m=21, 1y=252; percentile and z-score include today; flag at \|z\| > 2 or a 5-day move above 10 bp (IG), 25 bp (HY, BB, B), 50 bp (CCC). |
| Spreads | FRED days with no value are dropped, never filled. |
| Issuers | Latest balance sheet (10-Q or 10-K); LTM = FY + YTD − prior YTD. |
| Issuers | Headline EBITDA = operating income + D&A + goodwill/intangible impairments − gains (+ losses) on business or disposal-group sales. Not company or credit-agreement "Adjusted EBITDA". |
| Issuers | Total debt = carrying value of long-term debt incl. current portion + separately reported short-term borrowings; non-recourse debt excluded. |
| Issuers | HGV operating income derived as revenues − total operating expenses. |
| Issuers | Missing prior-period XBRL facts are never treated as zero; gaps are filled only from printed filings via `manual_overrides.csv`. |
| Recovery | Default EBITDA = LTM adjusted EBITDA × (1 − 20%); admin & priority claims 5% of EV; multiples 4.0x / 5.5x / 7.0x, uniform across issuers and entities. |
| Recovery | Claims = principal only; revolvers at drawn amounts; no deficiency claims; leases and "other" debt are general unsecured claims. |
| Recovery | Lumen consolidated; BHC by legal entity with the simplifications listed above. |

## Limitations
- **Public data only.** No dealer color, no trade data, no live loan or bond prices (the
  optional prices file is not included), and no private credit-agreement terms beyond
  what is filed with the SEC.
- **Spread history is short.** FRED serves about three years of these ICE BofA series
  (2023-11-20 to 2026-11-18 in this run), so the "1-year" statistics sit inside a short,
  mostly benign sample; a z-score also stays high for a while in a steady trend because
  the trailing mean lags.
- **Simplified EBITDA.** Adjusted EBITDA here makes only two adjustments. It is not the
  company's or the lenders' definition, ignores leases (EBITDAR), and for HGV is struck
  after securitization interest, so HGV is not comparable with the others.
- **Point-in-time and backward-looking.** Ratios use last-twelve-months results and the
  latest balance sheet; they do not project earnings, free cash flow or liquidity.
  Lumen's LTM still includes a business it sold, which likely understates its leverage.
- **Rank-based composite.** The ranking averages ranks and ignores the size of
  differences; small gaps can change the order.
- **Recoveries are scenario estimates, not forecasts.** They depend most on the chosen
  multiples and EBITDA haircut, which are illustrative and uniform across very different
  industries. Claims exclude accrued interest and fees; deficiency claims, intercompany
  claims, litigation, pensions, trade claims and plan negotiations are ignored.
- **Legal structure is simplified.** Lumen is modelled on a consolidated basis because
  entity-level financials are not available; Bausch Health's entity model rests on the
  simplifications listed in Milestone 3; Six Flags' non-guarantor subsidiaries are not
  valued separately.
- **XBRL is imperfect.** Companies tag differently and sometimes inconsistently. Real
  cases handled in the code, each with a test in `tests/test_fundamentals.py`: a current
  year-to-date value with untagged prior periods (Bausch Health impairment), 10-Q maturity
  buckets that count from the year in progress, a stale maturity schedule (HGV), and a
  `LongTermDebt` tag that excluded the current portion (Six Flags). The tag audit and the
  debt reconciliation against each debt note are the controls, not a guarantee.
- **Ratings** come only from filings and one company release; ratings not disclosed in
  filings are marked "Not in filing" until checked on Bloomberg.
