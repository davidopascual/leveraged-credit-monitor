"""Spread statistics. Pure functions: pandas Series in, numbers out. No I/O.

Conventions
- Input series are OAS in PERCENT as FRED reports them (3.25 means 325 bp).
- Windows are counted in observations (trading days), not calendar days.
- "Today" means the last observation in the series.
- If there is not enough history for a window, the result is NaN (never guessed).
"""

from __future__ import annotations

import math

import pandas as pd

BP_PER_PERCENT = 100.0


def to_bp(pct: float) -> float:
    """Convert a spread in percent to basis points (1% = 100 bp)."""
    return pct * BP_PER_PERCENT


def change_bp(s: pd.Series, n: int) -> float:
    """Change in bp between the last observation and the one n observations earlier."""
    if len(s) <= n:
        return math.nan
    return to_bp(s.iloc[-1] - s.iloc[-1 - n])


def trailing_percentile(s: pd.Series, window: int) -> float:
    """Percent of the trailing `window` observations (today included) that are <= today.

    100 means today is the widest spread of the window; a value near 0 means the tightest.
    """
    if len(s) < window:
        return math.nan
    w = s.iloc[-window:]
    return float((w <= w.iloc[-1]).mean() * 100.0)


def trailing_zscore(s: pd.Series, window: int) -> float:
    """(today - mean) / sample std over the trailing `window` observations, today included."""
    if len(s) < window:
        return math.nan
    w = s.iloc[-window:]
    std = w.std(ddof=1)
    if std == 0 or math.isnan(std):
        return math.nan
    return float((w.iloc[-1] - w.mean()) / std)


def flag_reasons(z: float, move_5d_bp: float, z_threshold: float, move_threshold_bp: float) -> list[str]:
    """Why (if at all) a series is outside its recent range.

    Uses absolute values: a sharp tightening is also unusual and worth seeing.
    NaN inputs never trigger a flag.
    """
    reasons: list[str] = []
    if not math.isnan(z) and abs(z) > z_threshold:
        reasons.append(f"|z|>{z_threshold:g}")
    if not math.isnan(move_5d_bp) and abs(move_5d_bp) > move_threshold_bp:
        reasons.append(f"|5d|>{move_threshold_bp:g}bp")
    return reasons


def spread_difference(wide: pd.Series, narrow: pd.Series) -> pd.Series:
    """wide - narrow on dates where both exist (e.g. CCC - BB). Same units as inputs."""
    both = pd.concat([wide, narrow], axis=1, join="inner").dropna()
    return both.iloc[:, 0] - both.iloc[:, 1]


def summarize(
    name: str,
    s: pd.Series,
    windows: dict[str, int],
    z_threshold: float,
    move_threshold_bp: float,
) -> dict[str, object]:
    """One row of the daily summary table for a single series."""
    z = trailing_zscore(s, windows["y1"])
    d5 = change_bp(s, windows["d5"])
    reasons = flag_reasons(z, d5, z_threshold, move_threshold_bp)
    return {
        "series": name,
        "as_of": s.index[-1].date(),
        "level_bp": to_bp(s.iloc[-1]),
        "chg_1d_bp": change_bp(s, windows["d1"]),
        "chg_5d_bp": d5,
        "chg_1m_bp": change_bp(s, windows["m1"]),
        "pctile_1y": trailing_percentile(s, windows["y1"]),
        "z_1y": z,
        "flag": ", ".join(reasons) if reasons else "",
    }
