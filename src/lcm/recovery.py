"""Recovery scenarios per issuer, built on the pure waterfall in lcm.waterfall.

Default-scenario EBITDA = headline (adjusted) LTM EBITDA x (1 - haircut).
For each EV/EBITDA multiple: EV -> less admin % -> waterfall over the capital structure
in data/inputs/capital_structure.csv (non-recourse debt excluded).

Bausch Health is modelled by legal entity (see bhc_entity_waterfall); the other
issuers as a single borrowing group.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from lcm.waterfall import Claim, Payout, distributable_value, enterprise_value, run_waterfall

PRIORITY_LABELS = {
    0: "Receivables securitization",
    1: "First-lien secured",
    2: "Second-lien secured",
    3: "Senior unsecured & other",
    4: "Subordinated",
}


@dataclass
class ScenarioResult:
    ticker: str
    multiple: float
    default_ebitda: float
    ev: float
    admin: float
    distributable: float
    payouts: list[Payout]
    residual: float
    groups: dict[str, str] = field(default_factory=dict)   # claim name -> display group
    notes: list[str] = field(default_factory=list)


def default_ebitda(ltm_adjusted_ebitda: float, haircut: float) -> float:
    """Default-scenario EBITDA: LTM adjusted EBITDA reduced by `haircut` (e.g. 0.20 = -20%)."""
    if not 0.0 <= haircut < 1.0:
        raise ValueError("haircut must be in [0, 1)")
    return ltm_adjusted_ebitda * (1.0 - haircut)


def claims_from_rows(rows: pd.DataFrame) -> list[Claim]:
    """One claim per debt-note row, excluding non-recourse debt."""
    rec = rows[rows["class"] != "nonrecourse"]
    return [Claim(r["instrument"], float(r["principal_musd"]), int(r["priority"])) for _, r in rec.iterrows()]


def single_group_scenario(ticker: str, rows: pd.DataFrame, ebitda_def: float, multiple: float,
                          admin_pct: float) -> ScenarioResult:
    claims = claims_from_rows(rows)
    ev = enterprise_value(ebitda_def, multiple)
    dist = distributable_value(ebitda_def, multiple, admin_pct)
    r = run_waterfall(dist, claims)
    groups = {c.name: PRIORITY_LABELS[c.priority] for c in claims}
    return ScenarioResult(ticker, multiple, ebitda_def, ev, ev - dist, dist, r.payouts, r.residual, groups)


# --------------------------------------------------------------------------
# Bausch Health: three legal-entity boxes
# --------------------------------------------------------------------------

def bhc_entity_waterfall(
    ebitda_bl: float, ebitda_remainco: float, multiple: float, admin_pct: float,
    bl_claims: list[Claim], numberco_claims: list[Claim], remainco_claims: list[Claim],
    pct_bl_pledged_to_numberco: float, pct_bl_other_bhc: float,
) -> ScenarioResult:
    """Bausch Health by legal entity.

    1. Bausch + Lomb box: B+L value pays B+L's own debt; what is left is B+L equity.
    2. B+L equity is split by shareholding: 126NumberCo's pledged shares, other BHC-held
       shares, and public holders (who take their share; it never reaches BHC creditors).
    3. 126NumberCo box: the pledged-share value pays 126NumberCo's term loan and 2032
       notes pro rata. Any shortfall becomes a claim under their pari passu first-lien
       guarantee from BHC, ranking with BHC's existing first-lien notes. Any surplus
       goes up to BHC.
    4. BHC RemainCo box: BHC's own value (ex-B+L) + 126NumberCo surplus + other BHC-held
       B+L equity pays: first lien (existing 1L notes + 126NumberCo guarantee claims),
       then second lien, then unsecured.
    Admin % is taken off each box's EV separately.
    """
    if pct_bl_pledged_to_numberco + pct_bl_other_bhc > 1.0 + 1e-9:
        raise ValueError("ownership percentages exceed 100%")
    ev_bl = enterprise_value(ebitda_bl, multiple)
    ev_rc = enterprise_value(ebitda_remainco, multiple)
    dist_bl = distributable_value(ebitda_bl, multiple, admin_pct)
    dist_rc = distributable_value(ebitda_remainco, multiple, admin_pct)

    bl = run_waterfall(dist_bl, bl_claims)
    equity_bl = bl.residual
    stake_numberco = equity_bl * pct_bl_pledged_to_numberco
    stake_other_bhc = equity_bl * pct_bl_other_bhc
    to_public = equity_bl - stake_numberco - stake_other_bhc

    nc = run_waterfall(stake_numberco, numberco_claims)
    guarantee_claims = [Claim(p.name, p.amount - p.paid, 1) for p in nc.payouts if p.amount - p.paid > 1e-9]

    rc_value = dist_rc + nc.residual + stake_other_bhc
    rc = run_waterfall(rc_value, remainco_claims + guarantee_claims)
    rc_by_name = {p.name: p for p in rc.payouts}

    payouts: list[Payout] = list(bl.payouts)
    for p in nc.payouts:                              # 126NumberCo: share collateral + guarantee
        extra = rc_by_name[p.name].paid if p.name in rc_by_name else 0.0
        payouts.append(Payout(p.name, p.amount, p.priority, p.paid + extra))
    nc_names = {c.name for c in numberco_claims}
    payouts += [p for p in rc.payouts if p.name not in nc_names]

    groups = {c.name: "B+L secured" if c.priority == 1 else "B+L unsecured & other" for c in bl_claims}
    groups.update({c.name: "126NumberCo 1L (B+L shares + BHC guarantee)" for c in numberco_claims})
    groups.update({c.name: {1: "BHC existing first lien", 2: "BHC second lien",
                            3: "BHC unsecured & other"}[c.priority] for c in remainco_claims})
    notes = [
        f"B+L distributable {dist_bl:,.0f}; B+L equity after its debt {equity_bl:,.0f} "
        f"(126NumberCo {stake_numberco:,.0f}, other BHC {stake_other_bhc:,.0f}, public {to_public:,.0f})",
        f"126NumberCo shortfall claimed under BHC guarantee {sum(c.amount for c in guarantee_claims):,.0f}; "
        f"surplus to BHC {nc.residual:,.0f}",
        f"BHC RemainCo value {rc_value:,.0f} = own {dist_rc:,.0f} + 126NumberCo surplus {nc.residual:,.0f} "
        f"+ other BHC-held B+L equity {stake_other_bhc:,.0f}",
    ]
    return ScenarioResult(
        "BHC", multiple, ebitda_bl + ebitda_remainco, ev_bl + ev_rc, (ev_bl - dist_bl) + (ev_rc - dist_rc),
        dist_bl + dist_rc, payouts, rc.residual, groups, notes,
    )


def split_bhc_rows(rows: pd.DataFrame) -> tuple[list[Claim], list[Claim], list[Claim]]:
    """Assign each BHC instrument to its legal-entity box using the `entity` column."""
    rec = rows[rows["class"] != "nonrecourse"]
    is_bl = rec["entity"].str.contains("Bausch + Lomb", regex=False)
    is_nc = rec["entity"].str.contains("126NumberCo", regex=False)
    return claims_from_rows(rec[is_bl]), claims_from_rows(rec[is_nc]), claims_from_rows(rec[~is_bl & ~is_nc])


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def instrument_table(results: list[ScenarioResult]) -> pd.DataFrame:
    """One row per instrument, one recovery column per multiple (in %)."""
    rows: dict[tuple, dict] = {}
    for res in results:
        for p in res.payouts:
            key = (res.ticker, p.name)
            row = rows.setdefault(key, {"ticker": res.ticker, "group": res.groups.get(p.name, ""),
                                        "instrument": p.name, "priority": p.priority, "principal_musd": p.amount})
            row[f"recovery_{res.multiple:g}x"] = p.recovery * 100
    return pd.DataFrame(rows.values())


def group_table(results: list[ScenarioResult]) -> pd.DataFrame:
    """Recovery by display group (instruments in a group are pari passu, so they share one rate)."""
    out: dict[tuple, dict] = {}
    for res in results:
        agg: dict[str, list[float]] = {}
        order: dict[str, int] = {}
        for p in res.payouts:
            g = res.groups.get(p.name, "")
            a = agg.setdefault(g, [0.0, 0.0])
            a[0] += p.amount
            a[1] += p.paid
            order[g] = min(order.get(g, 99), p.priority)
        for g, (amt, paid) in agg.items():
            box = {"B+L": 0, "126NumberCo": 1, "BHC": 2}.get(g.split(" ")[0], 0)   # entity, then seniority
            row = out.setdefault((res.ticker, g), {"ticker": res.ticker, "group": g, "priority": order[g],
                                                  "display_order": box * 10 + order[g], "principal_musd": amt})
            row[f"recovery_{res.multiple:g}x"] = paid / amt * 100 if amt > 0 else math.nan
    return pd.DataFrame(out.values())


def scenario_table(results: list[ScenarioResult]) -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": r.ticker, "multiple": r.multiple, "default_ebitda_musd": r.default_ebitda, "ev_musd": r.ev,
        "admin_musd": r.admin, "distributable_musd": r.distributable, "paid_musd": sum(p.paid for p in r.payouts),
        "claims_musd": sum(p.amount for p in r.payouts), "residual_equity_musd": r.residual,
    } for r in results])


def compare_prices(instruments: pd.DataFrame, prices: pd.DataFrame, base_col: str) -> pd.DataFrame:
    """Price (per 100 face) vs scenario recoveries, and relative value within each issuer.

    cushion = base-case recovery - price (percentage points). Within an issuer, the
    tranche with the highest cushion looks cheapest relative to the others, the lowest
    looks richest. This is relative to this model's scenarios, not a fair-value call.
    """
    m = instruments.merge(prices[["ticker", "instrument", "price", "price_date", "source"]],
                          on=["ticker", "instrument"], how="inner")
    m["cushion_pts"] = m[base_col] - m["price"]
    m["rank_cheap_within_issuer"] = m.groupby("ticker")["cushion_pts"].rank(ascending=False, method="min")
    return m


# --------------------------------------------------------------------------
# Coverage multiples (no multiple assumption)
# --------------------------------------------------------------------------

def coverage_rows(ticker: str, claims: list[Claim], groups: dict[str, str], ebitda: float,
                  scenario_factor: float, basis: str, extra_order: int = 0) -> list[dict]:
    """Cumulative principal through each class / EBITDA, in seniority order.

    covered_at_x: the EV/EBITDA multiple at which the class (and everything senior to it)
    is fully covered, before any haircut or admin claims. No multiple is assumed.
    covered_at_x_in_scenarios: the same on the scenario basis, i.e. divided by
    EBITDA x scenario_factor, where scenario_factor = (1 - haircut) x (1 - admin %).
    A class recovers 100% in any scenario whose multiple is >= this number.
    """
    by_group: dict[str, list] = {}
    for c in claims:
        g = groups[c.name]
        e = by_group.setdefault(g, [c.priority, 0.0])
        e[0] = min(e[0], c.priority)
        e[1] += c.amount
    rows, cum = [], 0.0
    for g, (prio, amt) in sorted(by_group.items(), key=lambda kv: kv[1][0]):
        cum += amt
        ok = ebitda > 0
        rows.append({
            "ticker": ticker, "group": g, "priority": prio, "display_order": extra_order + prio,
            "principal_musd": amt, "cumulative_musd": cum, "ebitda_basis": basis, "ebitda_musd": ebitda,
            "covered_at_x": cum / ebitda if ok else math.nan,
            "covered_at_x_in_scenarios": cum / (ebitda * scenario_factor) if ok else math.nan,
        })
    return rows


def coverage_single_group(ticker: str, rows: pd.DataFrame, adj_ebitda: float, scenario_factor: float) -> list[dict]:
    claims = claims_from_rows(rows)
    groups = {c.name: PRIORITY_LABELS[c.priority] for c in claims}
    return coverage_rows(ticker, claims, groups, adj_ebitda, scenario_factor, "LTM adjusted EBITDA")


def coverage_bhc(rows: pd.DataFrame, bl_ebitda: float, remainco_ebitda: float, scenario_factor: float) -> list[dict]:
    """B+L classes vs B+L EBITDA; 126NumberCo and BHC classes vs BHC ex-B+L EBITDA.

    126NumberCo's debt is counted in BHC's first lien because of its pari passu
    first-lien guarantee; no value is credited for B+L equity.
    """
    bl, nc, rc = split_bhc_rows(rows)
    bl_groups = {c.name: "B+L secured" if c.priority == 1 else "B+L unsecured & other" for c in bl}
    out = coverage_rows("BHC", bl, bl_groups, bl_ebitda, scenario_factor, "B+L LTM adjusted EBITDA", 0)
    rc_all = rc + nc
    rc_groups = {c.name: {1: "BHC first lien (existing 1L + 126NumberCo guarantee)", 2: "BHC second lien",
                          3: "BHC unsecured & other"}[c.priority] for c in rc_all}
    out += coverage_rows("BHC", rc_all, rc_groups, remainco_ebitda, scenario_factor,
                         "BHC ex-B+L LTM adjusted EBITDA (no B+L equity credited)", 20)
    return out
