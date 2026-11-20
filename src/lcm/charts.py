"""Matplotlib charts saved as PNG to outputs/."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed
import matplotlib.pyplot as plt
import pandas as pd

from lcm.spreads import to_bp

# Fixed categorical order (colour follows the series, never its rank).
SERIES_COLORS = {
    "IG": "#2a78d6",
    "HY": "#eb6834",
    "BB": "#1baf7a",
    "B": "#eda100",
    "CCC": "#e87ba4",
}
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e4e3df"


def _style(ax: plt.Axes, title: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=12, color=INK)
    ax.set_ylabel(ylabel, color=INK_MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED)


def _spread_labels(values: dict[str, float], min_gap: float) -> dict[str, float]:
    """Nudge end-of-line label positions upward so no two are closer than min_gap.

    Only the label moves; the plotted data is untouched.
    """
    placed: dict[str, float] = {}
    last = float("-inf")
    for name, v in sorted(values.items(), key=lambda kv: kv[1]):
        y = max(v, last + min_gap)
        placed[name] = y
        last = y
    return placed


def plot_oas_levels(series: dict[str, pd.Series], out: Path) -> Path:
    """All OAS series in bp on one axis, direct-labelled at the right edge."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ends: dict[str, tuple[pd.Timestamp, float]] = {}
    for name, s in series.items():
        bp = s.map(to_bp)
        ax.plot(bp.index, bp.values, color=SERIES_COLORS.get(name), linewidth=1.6, label=name)
        ends[name] = (bp.index[-1], bp.iloc[-1])
    ymax = max(s.max() for s in series.values()) * 100
    label_y = _spread_labels({n: v for n, (_, v) in ends.items()}, min_gap=ymax * 0.035)
    for name, (x, y) in ends.items():
        ax.annotate(f"{name} {y:.0f}", xy=(x, label_y[name]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
    _style(ax, "ICE BofA US option-adjusted spreads", "OAS (bp)")
    ax.legend(frameon=False, ncol=len(series), loc="upper left")
    ax.set_ylim(bottom=0)
    fig.text(0.01, 0.01, "Source: ICE BofA via FRED", fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.03, 0.95, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_ccc_minus_bb(diff_pct: pd.Series, window: int, out: Path) -> Path:
    """CCC-BB differential in bp with its trailing mean and +/-2 std band."""
    bp = diff_pct.map(to_bp)
    mean = bp.rolling(window).mean()
    std = bp.rolling(window).std(ddof=1)

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.fill_between(bp.index, mean - 2 * std, mean + 2 * std, color=SERIES_COLORS["CCC"],
                    alpha=0.12, linewidth=0, label=f"trailing {window}-obs mean ±2σ")
    ax.plot(mean.index, mean.values, color=INK_MUTED, linewidth=1, linestyle="--",
            label=f"trailing {window}-obs mean")
    ax.plot(bp.index, bp.values, color=SERIES_COLORS["CCC"], linewidth=1.8, label="CCC − BB")
    ax.annotate(f"{bp.iloc[-1]:.0f} bp", xy=(bp.index[-1], bp.iloc[-1]), xytext=(6, 0),
                textcoords="offset points", va="center", fontsize=9, color=INK)
    _style(ax, "CCC-and-lower minus BB OAS: stress in the weakest credits", "Differential (bp)")
    ax.legend(frameon=False, loc="upper left")
    fig.text(0.01, 0.01, "Source: ICE BofA via FRED (BAMLH0A3HYC − BAMLH0A1HYBB)",
             fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.03, 0.95, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_maturity_wall(ticker: str, buckets: pd.DataFrame, as_of: str, source: str, out: Path) -> Path:
    """Scheduled principal by fiscal year (bars), labelled in $bn."""
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.set_axisbelow(True)  # gridlines behind the bars
    x = list(range(len(buckets)))
    bn = buckets["principal_musd"] / 1000
    ax.bar(x, bn, color=SERIES_COLORS["IG"], width=0.6)
    for xi, v in zip(x, bn):
        ax.annotate(f"{v:,.1f}", xy=(xi, v), xytext=(0, 3), textcoords="offset points",
                    ha="center", fontsize=9, color=INK)
    ax.set_xticks(x, buckets["bucket"])
    _style(ax, f"{ticker}: debt maturity wall (scheduled principal, as of {as_of})", "$ billions")
    ax.set_ylim(0, bn.max() * 1.15 if len(bn) else 1)
    fig.text(0.01, 0.01, f"Source: SEC EDGAR XBRL ({source})", fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
