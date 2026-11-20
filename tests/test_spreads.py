"""Hand-checkable tests for the spread statistics. Expected values worked out on paper."""

import math

import pandas as pd
import pytest

from lcm.fred import parse
from lcm.spreads import (
    change_bp,
    flag_reasons,
    spread_difference,
    summarize,
    to_bp,
    trailing_percentile,
    trailing_zscore,
)


def s(values, start="2024-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


# ---- units / changes -------------------------------------------------------

def test_to_bp():
    assert to_bp(3.25) == pytest.approx(325.0)


def test_change_bp():
    x = s([3.00, 3.10, 3.25])
    assert change_bp(x, 1) == pytest.approx(15.0)   # 3.25 - 3.10 = 0.15% = 15 bp
    assert change_bp(x, 2) == pytest.approx(25.0)   # 3.25 - 3.00
    assert math.isnan(change_bp(x, 3))              # not enough history


# ---- z-score ---------------------------------------------------------------

def test_zscore_basic():
    # mean 3, sample std sqrt(2.5) = 1.5811 -> z = 2 / 1.5811 = 1.2649
    assert trailing_zscore(s([1, 2, 3, 4, 5]), 5) == pytest.approx(2 / math.sqrt(2.5))


def test_zscore_uses_only_trailing_window():
    # window 3 -> [1, 2, 3]: mean 2, std 1, z = 1. The 10 must be ignored.
    assert trailing_zscore(s([10, 1, 2, 3]), 3) == pytest.approx(1.0)


def test_zscore_negative_when_tight():
    # [5,4,3,2,1]: mean 3, std 1.5811 -> z = -1.2649
    assert trailing_zscore(s([5, 4, 3, 2, 1]), 5) == pytest.approx(-2 / math.sqrt(2.5))


def test_zscore_constant_series_is_nan():
    assert math.isnan(trailing_zscore(s([2, 2, 2]), 3))


def test_zscore_short_history_is_nan():
    assert math.isnan(trailing_zscore(s([1, 2]), 3))


# ---- percentile ------------------------------------------------------------

def test_percentile_widest_is_100():
    assert trailing_percentile(s([3, 1, 4, 1, 5]), 5) == pytest.approx(100.0)


def test_percentile_tightest_counts_only_itself():
    assert trailing_percentile(s([5, 4, 3, 2, 1]), 5) == pytest.approx(20.0)


def test_percentile_middle():
    # [1, 3, 2]: values <= 2 are {1, 2} -> 2/3
    assert trailing_percentile(s([1, 3, 2]), 3) == pytest.approx(200 / 3)


def test_percentile_ties_count_as_at_or_below():
    assert trailing_percentile(s([2, 2, 2]), 3) == pytest.approx(100.0)


def test_percentile_short_history_is_nan():
    assert math.isnan(trailing_percentile(s([1, 2]), 3))


# ---- flags -----------------------------------------------------------------

def test_no_flag_inside_range():
    assert flag_reasons(z=1.5, move_5d_bp=10, z_threshold=2, move_threshold_bp=25) == []


def test_flag_on_z():
    assert flag_reasons(2.1, 0, 2, 25) == ["|z|>2"]
    assert flag_reasons(-2.1, 0, 2, 25) == ["|z|>2"]


def test_flag_threshold_is_strict():
    assert flag_reasons(2.0, 25, 2, 25) == []


def test_flag_on_5d_move_either_direction():
    assert flag_reasons(0, 30, 2, 25) == ["|5d|>25bp"]
    assert flag_reasons(0, -30, 2, 25) == ["|5d|>25bp"]


def test_flag_both():
    assert flag_reasons(3, 40, 2, 25) == ["|z|>2", "|5d|>25bp"]


def test_nan_never_flags():
    assert flag_reasons(math.nan, math.nan, 2, 25) == []


def test_summarize_flags_a_spike():
    # 9 flat-ish days then a 60 bp jump: z is large and the 5d move is 60 bp.
    x = s([3.00, 3.01, 3.00, 3.01, 3.00, 3.01, 3.00, 3.01, 3.00, 3.60])
    row = summarize("HY", x, {"d1": 1, "d5": 5, "m1": 21, "y1": 10}, 2.0, 25)
    assert row["level_bp"] == pytest.approx(360)
    assert row["chg_5d_bp"] == pytest.approx(60)    # 3.60 - 3.00 (5 rows back)
    assert math.isnan(row["chg_1m_bp"])             # only 10 observations
    assert row["pctile_1y"] == pytest.approx(100)
    assert row["flag"] == "|z|>2, |5d|>25bp"


# ---- CCC - BB --------------------------------------------------------------

def test_spread_difference_aligns_on_common_dates():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    ccc = pd.Series([9.0, 9.5, 10.0], index=idx)
    bb = pd.Series([2.0, 3.0], index=idx[1:])        # BB missing on the first day
    d = spread_difference(ccc, bb)
    assert list(d.index) == list(idx[1:])
    assert list(d.values) == pytest.approx([7.5, 7.0])  # 9.5-2.0, 10.0-3.0


# ---- FRED parsing ----------------------------------------------------------

def test_parse_drops_and_counts_missing():
    raw = {
        "realtime_start": "2026-11-19",
        "observations": [
            {"date": "2024-01-02", "value": "3.10"},
            {"date": "2024-01-03", "value": "."},
            {"date": "2024-01-04", "value": "3.20"},
        ],
    }
    fs = parse(raw, "TEST")
    assert fs.n_missing == 1
    assert list(fs.values.values) == pytest.approx([3.10, 3.20])
    assert fs.fetched_on == "2026-11-19"
