"""Matplotlib charts saved as PNG to outputs/."""

from __future__ import annotations

import textwrap
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


def plot_leverage_comparison(table: pd.DataFrame, out: Path) -> Path:
    """Two panels: Debt/EBITDA and EBITDA/interest, adjusted (headline) vs reported, per issuer.

    Issuers appear in headline (adjusted) rank order, most stretched at the top.
    A ratio that is not meaningful (EBITDA <= 0) is labelled instead of drawn.
    """
    order = list(table["ticker"])[::-1]          # most stretched at top
    t = table.set_index("ticker").loc[order]
    y = range(len(order))
    h = 0.38
    fig, axes = plt.subplots(1, 2, figsize=(11, 0.9 * len(order) + 1.8), sharey=True)
    panels = [
        (axes[0], "debt_to_ebitda_adj", "debt_to_ebitda_reported", "Debt / EBITDA (higher = more levered)"),
        (axes[1], "ebitda_to_interest_adj", "ebitda_to_interest_reported", "EBITDA / interest (lower = weaker)"),
    ]
    for ax, adj_col, rep_col, title in panels:
        ax.set_axisbelow(True)
        for offset, col, color, label in [(h / 2, adj_col, SERIES_COLORS["IG"], "Adjusted (headline)"),
                                          (-h / 2, rep_col, SERIES_COLORS["HY"], "Reported")]:
            vals = t[col]
            ax.barh([yi + offset for yi in y], vals.fillna(0), height=h, color=color, label=label)
            for yi, v in zip(y, vals):
                txt = "n.m. (EBITDA ≤ 0)" if pd.isna(v) else f"{v:.2f}x"
                ax.annotate(txt, xy=(0 if pd.isna(v) else max(v, 0), yi + offset), xytext=(4, 0),
                            textcoords="offset points", va="center", fontsize=8, color=INK)
        ax.axvline(0, color=INK_MUTED, linewidth=0.8)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=INK_MUTED)
        lo, hi = ax.get_xlim()
        ax.set_xlim(min(lo, 0), hi * 1.25)
    axes[0].set_yticks(list(y), order)
    axes[0].legend(frameon=False, loc="lower right", fontsize=8)
    fig.text(0.01, 0.01, "Source: SEC EDGAR XBRL, LTM. Adjusted = + impairments − gains on business/disposal-group sales. "
             "Order = headline (adjusted) rank.", fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


RECOVERY_SHADES = ["#9ec5f4", "#4f93e0", "#1d5aa8"]   # one hue, light -> dark = low -> high multiple


def plot_recoveries(ticker: str, groups: pd.DataFrame, multiples: list[float], subtitle: str, out: Path) -> Path:
    """Recovery % by claim group (seniority order) for each EV/EBITDA scenario."""
    g = groups.sort_values("display_order" if "display_order" in groups else "priority").reset_index(drop=True)
    n = len(multiples)
    w = 0.8 / n
    fig, ax = plt.subplots(figsize=(max(8, 1.9 * len(g) + 2), 4.8))
    ax.set_axisbelow(True)
    for k, mlt in enumerate(multiples):
        col = f"recovery_{mlt:g}x"
        xs = [i + (k - (n - 1) / 2) * w for i in range(len(g))]
        ax.bar(xs, g[col], width=w * 0.92, color=RECOVERY_SHADES[k % len(RECOVERY_SHADES)], label=f"{mlt:g}x EBITDA")
        for xi, v in zip(xs, g[col]):
            ax.annotate(f"{v:.0f}%", xy=(xi, v), xytext=(0, 2), textcoords="offset points",
                        ha="center", fontsize=8, color=INK)
    def size(amt: float) -> str:
        return f"${amt / 1000:,.1f}bn" if amt >= 1000 else f"${amt:,.0f}M"
    labels = [textwrap.fill(grp, 18) + f"\n{size(amt)}" for grp, amt in zip(g["group"], g["principal_musd"])]
    ax.set_xticks(range(len(g)), labels, fontsize=8)
    ax.set_ylim(0, 125)
    ax.set_yticks(range(0, 101, 20))
    _style(ax, f"{ticker}: scenario recoveries by seniority", "Recovery (% of principal)")
    ax.legend(frameon=False, loc="upper center", ncol=len(multiples), fontsize=8)
    fig.text(0.01, 0.01, subtitle, fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
