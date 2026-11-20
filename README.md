# Leveraged Credit Monitor

A Python toolkit for monitoring US high-yield credit: spread regime monitoring, issuer
credit snapshots from SEC filings, and recovery waterfall scenarios.

> Work in progress. Milestone 1 (spread monitor) is complete. Full methodology,
> assumptions and limitations will be documented at Milestone 4.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # then add your free FRED API key
```

## Milestone 1: credit spread monitor

```bash
.venv/bin/python -m lcm spreads            # uses cached data in data/raw/ if present
.venv/bin/python -m lcm spreads --refresh  # re-download from FRED
.venv/bin/pytest
```

Data: ICE BofA option-adjusted spreads from FRED (IG `BAMLC0A0CM`, HY `BAMLH0A0HYM2`,
BB `BAMLH0A1HYBB`, B `BAMLH0A2HYB`, CCC & lower `BAMLH0A3HYC`). FRED serves only about
the last three years of these series; the CLI prints the exact date range it received.

Per series it reports the current level, 1-day / 5-day / 1-month change (bp),
percentile and z-score versus the trailing 252 observations, and flags the series when
|z| > 2 or the 5-day move exceeds a per-series threshold (see `config.yaml`). It also
tracks the CCC-minus-BB differential. Outputs go to `outputs/`.
