"""SEC EDGAR client: polite (User-Agent + rate limit) and cached.

SEC fair-access rules: identify yourself in the User-Agent and stay under
10 requests per second. We stay well under that with a minimum gap between calls.
Every response is cached under data/raw/sec/ so re-runs hit the network zero times.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

MIN_SECONDS_BETWEEN_REQUESTS = 0.2   # 5 req/s, half the SEC limit

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def get(url: str, user_agent: str, cache_path: Path, refresh: bool = False) -> bytes:
    """GET a URL once and cache the raw bytes. Later calls read the cache."""
    if cache_path.exists() and not refresh:
        return cache_path.read_bytes()
    if not user_agent:
        raise RuntimeError("SEC_USER_AGENT is not set (needs 'Name email'). Put it in .env.")
    _throttle()
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=60)
    resp.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    return resp.content


def cik10(cik: int) -> str:
    return f"{cik:010d}"


def company_facts(cik: int, user_agent: str, cache_dir: Path, refresh: bool = False) -> dict:
    """XBRL companyfacts: every tagged number the company has filed."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10(cik)}.json"
    return json.loads(get(url, user_agent, cache_dir / f"companyfacts_{cik10(cik)}.json", refresh))


def submissions(cik: int, user_agent: str, cache_dir: Path, refresh: bool = False) -> dict:
    """Filing index for a company (form types, dates, accession numbers, primary docs)."""
    url = f"https://data.sec.gov/submissions/CIK{cik10(cik)}.json"
    return json.loads(get(url, user_agent, cache_dir / f"submissions_{cik10(cik)}.json", refresh))


def latest_filings(subs: dict, form: str, n: int = 1) -> list[dict]:
    """Most recent `n` filings of a form type, newest first."""
    recent = subs["filings"]["recent"]
    rows = [
        {
            "form": recent["form"][i],
            "filing_date": recent["filingDate"][i],
            "report_date": recent["reportDate"][i],
            "accession": recent["accessionNumber"][i],
            "primary_doc": recent["primaryDocument"][i],
        }
        for i in range(len(recent["form"]))
        if recent["form"][i] == form
    ]
    return rows[:n]


def filing_doc_url(cik: int, accession: str, primary_doc: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession.replace('-', '')}/{primary_doc}"
    )


def filing_document(
    cik: int, filing: dict, user_agent: str, cache_dir: Path, refresh: bool = False
) -> bytes:
    url = filing_doc_url(cik, filing["accession"], filing["primary_doc"])
    return get(url, user_agent, cache_dir / "filings" / f"{filing['accession']}_{filing['primary_doc']}", refresh)
