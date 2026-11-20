"""Smoke test: the dashboard renders without errors for every issuer selection.

Needs the CLI outputs (outputs/) and Streamlit; skipped otherwise.
"""

import pytest

from lcm.config import OUTPUT_DIR, ROOT

st_testing = pytest.importorskip("streamlit.testing.v1")
NEEDED = ["spread_summary.csv", "issuer_snapshot.csv", "coverage_multiples.csv", "recovery_by_group.csv"]


@pytest.mark.skipif(not all((OUTPUT_DIR / f).exists() for f in NEEDED),
                    reason="run `python -m lcm spreads|issuers|recovery` first")
def test_dashboard_renders_for_every_issuer():
    at = st_testing.AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=120).run()
    assert not at.exception
    assert [h.value for h in at.header] == [
        "1. Credit spread monitor", "2. Issuer credit snapshots", "3. Recovery scenarios"]
    for ticker in at.selectbox(key="rec").options:
        at.selectbox(key="rec").set_value(ticker).run()
        assert not at.exception, ticker
