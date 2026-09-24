"""``config/scoring.yaml``: the one home for scoring thresholds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SCORING_RELPATH = Path("config") / "scoring.yaml"


class ScoringConfigError(Exception):
    """``config/scoring.yaml`` is missing or malformed."""


def load_scoring(path: Path) -> dict[str, Any]:
    """The parsed scoring config at ``path``."""
    if not path.is_file():
        raise ScoringConfigError(f"scoring config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise ScoringConfigError(f"{path} must contain a YAML mapping at the top level")
    return doc


def shorts_max_seconds(config: dict[str, Any]) -> int:
    """The Shorts cut-off in seconds: a video at or under it is a Short."""
    value = config.get("shorts_max_seconds")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScoringConfigError(
            f"shorts_max_seconds must be a positive integer in scoring.yaml, got {value!r}"
        )
    return value
