"""Read values out of an SEC XBRL companyfacts document.

Two kinds of facts:
- instant facts (balance sheet): a value AS OF a date, e.g. debt on 2026-09-30.
- duration facts (income / cash flow): a value OVER a period, e.g. operating
  income from 2025-10-01 to 2026-09-30. 10-Qs report these year-to-date.

Values in companyfacts are already scaled to plain units (USD, not thousands or
millions), whatever scale the company used in its printed tables.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

ANNUAL_DAYS = (350, 380)    # 52/53-week fiscal years land in this range
DATE_TOLERANCE_DAYS = 7     # fiscal quarters often end on a Saturday, not month-end


@dataclass(frozen=True)
class Fact:
    start: date | None
    end: date
    val: float
    form: str
    filed: date
    accn: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days if self.start else 0


@dataclass(frozen=True)
class LTM:
    value: float
    method: str             # "FY" or "FY + YTD - prior YTD"
    periods: str            # human-readable description of the facts used


def _d(s: str) -> date:
    return date.fromisoformat(s)


def facts_for(companyfacts: dict, tag: str, taxonomy: str = "us-gaap", unit: str = "USD") -> list[Fact]:
    """All facts for one tag, de-duplicated: for each period keep the latest-filed value.

    The same period appears in several filings (a 10-K repeats prior years);
    taking the latest filing picks up restatements.
    """
    try:
        rows = companyfacts["facts"][taxonomy][tag]["units"][unit]
    except KeyError:
        return []
    best: dict[tuple, Fact] = {}
    for r in rows:
        f = Fact(
            start=_d(r["start"]) if "start" in r else None,
            end=_d(r["end"]),
            val=float(r["val"]),
            form=r.get("form", ""),
            filed=_d(r["filed"]),
            accn=r.get("accn", ""),
        )
        key = (f.start, f.end)
        if key not in best or f.filed > best[key].filed:
            best[key] = f
    return sorted(best.values(), key=lambda f: (f.end, f.days))


def _near(a: date, b: date, tol: int = DATE_TOLERANCE_DAYS) -> bool:
    return abs((a - b).days) <= tol


def instant(facts: list[Fact], end: date) -> Fact | None:
    """Balance-sheet value as of `end` (exact date match)."""
    hits = [f for f in facts if f.start is None and f.end == end]
    return hits[-1] if hits else None


def ltm(facts: list[Fact], end: date) -> LTM | None:
    """Last-twelve-months value of a flow item ending at `end`.

    - If a full fiscal year ends at `end`, LTM is that year.
    - Otherwise LTM = prior fiscal year + current YTD - prior-year YTD.
    Returns None (never a guess) if any piece is missing.
    """
    dur = [f for f in facts if f.start is not None]
    ending = [f for f in dur if f.end == end]
    annual = [f for f in ending if ANNUAL_DAYS[0] <= f.days <= ANNUAL_DAYS[1]]
    if annual:
        f = annual[-1]
        return LTM(f.val, "FY", f"FY {f.start}..{f.end}")

    ytds = [f for f in ending if f.days < ANNUAL_DAYS[0]]
    if not ytds:
        return None
    ytd = max(ytds, key=lambda f: f.days)  # longest = year-to-date, not the single quarter

    fy_prior = [
        f for f in dur
        if ANNUAL_DAYS[0] <= f.days <= ANNUAL_DAYS[1] and _near(f.end, ytd.start - timedelta(days=1))
    ]
    ytd_prior = [
        f for f in dur
        if _near(f.end, ytd.end - timedelta(days=365)) and _near(f.start, ytd.start - timedelta(days=365))
    ]
    if not fy_prior or not ytd_prior:
        return None
    fy, py = fy_prior[-1], ytd_prior[-1]
    return LTM(
        fy.val + ytd.val - py.val,
        "FY + YTD - prior YTD",
        f"FY {fy.start}..{fy.end} + YTD {ytd.start}..{ytd.end} - YTD {py.start}..{py.end}",
    )


def nan_if_none(x: float | None) -> float:
    return math.nan if x is None else x
