"""Waterfall tests: the properties any correct recovery waterfall must satisfy."""

import itertools

import pytest

from lcm.waterfall import Claim, distributable_value, enterprise_value, run_waterfall

# A TDG-shaped structure in $M: securitization, first lien, unsecured, subordinated.
STRUCTURE = [
    Claim("securitization", 875, 0),
    Claim("term loans", 12_803, 1),
    Claim("secured notes", 12_669, 1),
    Claim("leases & other", 352, 3),
    Claim("subordinated notes", 8_300, 4),
]
VALUES = [0, 500, 875, 10_000, 26_347, 26_500, 30_000, 34_999, 50_000]


@pytest.mark.parametrize("value", VALUES)
def test_recoveries_never_exceed_100pct(value):
    for p in run_waterfall(value, STRUCTURE).payouts:
        assert 0.0 <= p.recovery <= 1.0 + 1e-12


@pytest.mark.parametrize("value", VALUES)
def test_total_payout_never_exceeds_distributable_value(value):
    r = run_waterfall(value, STRUCTURE)
    assert r.total_paid <= value + 1e-9
    assert r.total_paid + r.residual == pytest.approx(value)


@pytest.mark.parametrize("value", VALUES)
def test_seniors_paid_in_full_before_juniors_get_anything(value):
    r = run_waterfall(value, STRUCTURE)
    for senior, junior in itertools.product(r.payouts, r.payouts):
        if senior.priority < junior.priority and junior.paid > 0:
            assert senior.recovery == pytest.approx(1.0)


def test_zero_value_returns_zero_for_everyone():
    r = run_waterfall(0, STRUCTURE)
    assert all(p.paid == 0 and p.recovery == 0 for p in r.payouts)
    assert r.residual == 0


def test_negative_value_treated_as_zero():
    assert run_waterfall(-100, STRUCTURE).total_paid == 0


def test_pari_passu_claims_share_pro_rata():
    # 20,000 left after the 875 securitization goes to 1L in proportion 12,803 : 12,669.
    r = {p.name: p for p in run_waterfall(20_875, STRUCTURE).payouts}
    assert r["term loans"].recovery == pytest.approx(r["secured notes"].recovery)
    assert r["term loans"].recovery == pytest.approx(20_000 / (12_803 + 12_669))
    assert r["subordinated notes"].paid == 0


def test_hand_worked_example():
    # Value 1,000: secured 600 paid in full, unsecured A 300 and B 200 share the last 400.
    claims = [Claim("secured", 600, 1), Claim("A", 300, 3), Claim("B", 200, 3)]
    r = {p.name: p for p in run_waterfall(1_000, claims).payouts}
    assert r["secured"].recovery == 1.0
    assert r["A"].paid == pytest.approx(240) and r["B"].paid == pytest.approx(160)
    assert r["A"].recovery == pytest.approx(0.8)


def test_residual_equity_when_everyone_is_paid():
    r = run_waterfall(40_000, STRUCTURE)
    assert all(p.recovery == pytest.approx(1.0) for p in r.payouts)
    assert r.residual == pytest.approx(40_000 - 34_999)


def test_distributable_value_takes_admin_off_ev():
    assert enterprise_value(500, 5.5) == 2_750
    assert distributable_value(500, 5.5, 0.05) == pytest.approx(2_612.5)
    assert distributable_value(-100, 5.5, 0.05) == 0       # negative EBITDA -> no value


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        distributable_value(500, 5.5, 1.5)
    with pytest.raises(ValueError):
        run_waterfall(100, [Claim("x", -1, 1)])


# ---- Bausch Health entity cascade -------------------------------------------

from lcm.recovery import bhc_entity_waterfall, default_ebitda  # noqa: E402

BL = [Claim("BL term loan", 300, 1), Claim("BL other", 20, 3)]
NC = [Claim("NC term loan", 200, 1), Claim("NC notes", 400, 1)]
RC = [Claim("RC 1L notes", 150, 1), Claim("RC 2L notes", 50, 2), Claim("RC unsecured", 400, 3)]


def run_bhc(ebitda_bl, ebitda_rc, multiple=5.0, admin=0.0, pledged=0.6, other=0.25):
    return bhc_entity_waterfall(ebitda_bl, ebitda_rc, multiple, admin, BL, NC, RC, pledged, other)


def test_bhc_hand_worked_example():
    # B+L: 100 x 5 = 500; pays BL debt 320 -> equity 180.
    #   126NumberCo 60% = 108, other BHC 25% = 45, public 15% = 27.
    # 126NumberCo: 108 vs 600 owed -> 18% from shares; shortfall 492 becomes a BHC 1L guarantee claim.
    # RemainCo: 120 x 5 = 600 + 0 surplus + 45 = 645.
    #   1L pool = 150 existing + 492 guarantee = 642 -> paid in full; 3 left -> 2L gets 3 of 50.
    r = {p.name: p for p in run_bhc(100, 120).payouts}
    assert r["BL term loan"].recovery == pytest.approx(1.0)
    assert r["NC term loan"].recovery == pytest.approx(1.0)          # 18% shares + 82% guarantee
    assert r["RC 1L notes"].recovery == pytest.approx(1.0)
    assert r["RC 2L notes"].paid == pytest.approx(3.0)
    assert r["RC unsecured"].paid == 0


@pytest.mark.parametrize("e_bl,e_rc,mult", [(80, 60, 4.0), (100, 120, 5.0), (300, 400, 7.0)])
def test_bhc_value_is_conserved(e_bl, e_rc, mult):
    # Every dollar of distributable value goes to a creditor, BHC equity, or B+L's public holders.
    res = run_bhc(e_bl, e_rc, multiple=mult, admin=0.05)
    paid = sum(p.paid for p in res.payouts)
    bl_equity = max(e_bl * mult * 0.95 - 320, 0)          # B+L debt is 300 + 20
    public = bl_equity * (1 - 0.6 - 0.25)
    assert paid + res.residual + public == pytest.approx(res.distributable)


def test_numberco_recovers_at_least_as_much_as_bhc_first_lien():
    # 126NumberCo has the B+L shares AND a pari 1L guarantee, so it can't do worse.
    for e_bl, e_rc in [(50, 20), (80, 60), (100, 120), (0, 0), (200, 10)]:
        r = {p.name: p for p in run_bhc(e_bl, e_rc, multiple=4.0).payouts}
        assert r["NC notes"].recovery >= r["RC 1L notes"].recovery - 1e-12


def test_bhc_zero_value_returns_zero():
    assert all(p.paid == 0 for p in run_bhc(0, 0).payouts)


def test_bhc_recoveries_capped_at_100pct():
    for p in run_bhc(1_000, 1_000).payouts:
        assert p.recovery <= 1.0 + 1e-12


def test_ownership_over_100pct_rejected():
    with pytest.raises(ValueError):
        run_bhc(100, 100, pledged=0.8, other=0.3)


def test_default_ebitda_haircut():
    assert default_ebitda(649, 0.20) == pytest.approx(519.2)
    with pytest.raises(ValueError):
        default_ebitda(649, 1.0)


def test_price_comparison_ranks_cheapest_tranche_within_issuer():
    import pandas as pd
    from lcm.recovery import compare_prices
    instr = pd.DataFrame({
        "ticker": ["X", "X", "Y"], "instrument": ["TL", "Notes", "TL"],
        "recovery_5.5x": [100.0, 40.0, 70.0],
    })
    prices = pd.DataFrame({
        "ticker": ["X", "X", "Y", "Z"], "instrument": ["TL", "Notes", "TL", "unknown"],
        "price": [98.0, 30.0, 80.0, 50.0], "price_date": ["2026-11-20"] * 4, "source": ["test"] * 4,
    })
    out = compare_prices(instr, prices, "recovery_5.5x").set_index(["ticker", "instrument"])
    assert out.loc[("X", "Notes"), "cushion_pts"] == 10      # 40 recovery - 30 price
    assert out.loc[("X", "Notes"), "rank_cheap_within_issuer"] == 1
    assert out.loc[("X", "TL"), "rank_cheap_within_issuer"] == 2
    assert out.loc[("Y", "TL"), "cushion_pts"] == -10
    assert len(out) == 3                                     # unmatched price row is dropped


def test_coverage_multiples_are_cumulative_by_seniority():
    from lcm.recovery import PRIORITY_LABELS, coverage_rows
    claims = [Claim("TL", 1_474, 1), Claim("Secured notes", 850, 1), Claim("Unsecured", 2_600, 3)]
    groups = {c.name: PRIORITY_LABELS[c.priority] for c in claims}
    rows = coverage_rows("FUN", claims, groups, ebitda=649, scenario_factor=0.8 * 0.95, basis="test")
    assert [r["group"] for r in rows] == ["First-lien secured", "Senior unsecured & other"]
    assert rows[0]["covered_at_x"] == pytest.approx(2_324 / 649)
    assert rows[1]["covered_at_x"] == pytest.approx(4_924 / 649)
    assert rows[0]["covered_at_x_in_scenarios"] == pytest.approx(2_324 / (649 * 0.76))


def test_coverage_consistent_with_scenarios():
    # A class is fully covered in a scenario exactly when the multiple >= covered_at_x_in_scenarios.
    from lcm.recovery import PRIORITY_LABELS, coverage_rows
    claims = [Claim("TL", 1_474, 1), Claim("Secured notes", 850, 1), Claim("Unsecured", 2_600, 3)]
    groups = {c.name: PRIORITY_LABELS[c.priority] for c in claims}
    ebitda, h, a = 649, 0.20, 0.05
    cov = coverage_rows("FUN", claims, groups, ebitda, (1 - h) * (1 - a), "test")
    for m in (3.0, 4.0, 4.7, 5.5, 7.0, 10.0):
        res = run_waterfall(distributable_value(ebitda * (1 - h), m, a), claims)
        first_lien_full = all(p.recovery > 1 - 1e-9 for p in res.payouts if p.priority == 1)
        assert first_lien_full == (m >= cov[0]["covered_at_x_in_scenarios"] - 1e-9)
