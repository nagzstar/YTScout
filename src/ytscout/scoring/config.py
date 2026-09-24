"""``config/scoring.yaml``: the one home for scoring thresholds."""

from __future__ import annotations

from dataclasses import dataclass, fields
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


@dataclass(frozen=True)
class DiscoveryConfig:
    """``discovery:`` in scoring.yaml: the competitor similarity filter (006)."""

    size_band_factor: float
    small_own_subs: int
    small_band_max: int
    shorts_share_min: float
    keyword_overlap_min: int


def discovery_config(config: dict[str, Any]) -> DiscoveryConfig:
    """The ``discovery:`` section, every key required and non-negative."""
    section = config.get("discovery")
    if not isinstance(section, dict):
        raise ScoringConfigError("scoring.yaml has no `discovery:` mapping")
    values: dict[str, Any] = {}
    for f in fields(DiscoveryConfig):
        value = section.get(f.name)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ScoringConfigError(
                f"discovery.{f.name} must be a non-negative number in scoring.yaml, got {value!r}"
            )
        values[f.name] = int(value) if f.type == "int" else float(value)
    if values["size_band_factor"] < 1 or values["shorts_share_min"] > 1:
        raise ScoringConfigError(
            "discovery.size_band_factor must be ≥ 1 and shorts_share_min ≤ 1 in scoring.yaml"
        )
    return DiscoveryConfig(**values)
