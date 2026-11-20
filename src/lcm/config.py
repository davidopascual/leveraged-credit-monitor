"""Load config.yaml and secrets from the environment (or a git-ignored .env file)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "outputs"


def load_config(path: Path = ROOT / "config.yaml") -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def _read_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser so we don't need python-dotenv."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_secret(name: str) -> str | None:
    """Environment variable first, then .env in the project root."""
    return os.environ.get(name) or _read_dotenv(ROOT / ".env").get(name) or None
