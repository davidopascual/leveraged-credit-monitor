"""FRED client: download a series once, cache the raw JSON, parse it into pandas.

Raw responses are saved exactly as FRED returned them in data/raw/fred/<ID>.json,
so the pipeline re-runs offline and every number can be traced to a file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests


@dataclass
class FredSeries:
    series_id: str
    values: pd.Series        # float, in the units FRED reports (percent for OAS)
    n_missing: int           # observations FRED marked "." (no value), dropped
    fetched_on: str          # FRED's realtime_start = the date the data was pulled


def download(series_id: str, api_key: str, base_url: str, cache_dir: Path) -> Path:
    """Request the full available history for one series and write it to the cache."""
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json"}
    resp = requests.get(base_url, params=params, timeout=30)
    if resp.status_code != 200:
        # FRED puts the reason in error_message; never echo the URL (it contains the key).
        try:
            msg = resp.json().get("error_message", resp.text)
        except ValueError:
            msg = resp.text
        raise RuntimeError(f"FRED request for {series_id} failed ({resp.status_code}): {msg}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{series_id}.json"
    path.write_text(resp.text)
    return path


def parse(raw: dict, series_id: str) -> FredSeries:
    """Turn a FRED observations response into a date-indexed float Series.

    FRED uses "." for days with no value (e.g. market holidays). We drop those
    rows and report how many there were - we never fill them in.
    """
    obs = pd.DataFrame(raw["observations"])
    missing = obs["value"] == "."
    obs = obs.loc[~missing]
    values = pd.Series(
        obs["value"].astype(float).to_numpy(),
        index=pd.to_datetime(obs["date"]),
        name=series_id,
    ).sort_index()
    return FredSeries(series_id, values, int(missing.sum()), raw.get("realtime_start", "unknown"))


def load_series(
    series_id: str, api_key: str | None, base_url: str, cache_dir: Path, refresh: bool = False
) -> FredSeries:
    """Use the cached file unless refresh=True or there is no cache yet."""
    path = cache_dir / f"{series_id}.json"
    if refresh or not path.exists():
        if not api_key:
            raise RuntimeError(
                f"No cached data for {series_id} and FRED_API_KEY is not set. "
                "Export it or put it in .env."
            )
        download(series_id, api_key, base_url, cache_dir)
    return parse(json.loads(path.read_text()), series_id)
