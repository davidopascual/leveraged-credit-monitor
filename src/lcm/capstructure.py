"""Capital structure entered by hand from each company's debt note.

data/inputs/capital_structure.csv has one row per debt instrument, with the
filing, page and the units the filing used. Filings print tables in different
units (Six Flags: $ thousands; most others: $ millions), so every amount is
converted to $ millions before it is used anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

UNIT_TO_MILLIONS = {
    "usd": 1e-6,
    "thousands": 1e-3,
    "millions": 1.0,
    "billions": 1e3,
}

REQUIRED_COLUMNS = [
    "ticker", "entity", "instrument", "class", "priority", "principal", "units",
    "maturity", "form", "accession", "page",
]
CLASSES = {"secured", "unsecured", "subordinated", "nonrecourse", "other"}


def to_millions(amount: float, units: str) -> float:
    """Convert an amount printed in `units` to $ millions."""
    try:
        return amount * UNIT_TO_MILLIONS[units.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown units {units!r}; expected one of {sorted(UNIT_TO_MILLIONS)}")


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, comment="#", dtype={"accession": str, "page": str})
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing columns {missing}")
    bad = set(df["class"]) - CLASSES
    if bad:
        raise ValueError(f"unknown debt class(es) {bad}; expected {sorted(CLASSES)}")
    df["principal_musd"] = [to_millions(a, u) for a, u in zip(df["principal"], df["units"])]
    return df


def recourse_principal_musd(df: pd.DataFrame, ticker: str) -> float:
    """Sum of principal for one issuer, excluding non-recourse debt."""
    rows = df[(df["ticker"] == ticker) & (df["class"] != "nonrecourse")]
    return float(rows["principal_musd"].sum())
