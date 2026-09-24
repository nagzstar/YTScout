"""``config/scoring.yaml``: the one home for scoring thresholds."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from ytscout.scoring.metrics import LengthBucket, MetricsConfig

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

    subs_min: int
    subs_max: int
    hit_views_min: int
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
    if values["shorts_share_min"] > 1:
        raise ScoringConfigError("discovery.shorts_share_min must be ≤ 1 in scoring.yaml")
    if values["subs_max"] and values["subs_max"] < values["subs_min"]:
        raise ScoringConfigError("discovery.subs_max must be 0 (no cap) or ≥ subs_min")
    return DiscoveryConfig(**values)


def metrics_config(config: dict[str, Any]) -> MetricsConfig:
    """The ``competitor_metrics:`` section: outlier rule and length buckets."""
    section = config.get("competitor_metrics")
    if not isinstance(section, dict):
        raise ScoringConfigError("scoring.yaml has no `competitor_metrics:` mapping")
    multiplier = section.get("outlier_multiplier")
    if isinstance(multiplier, bool) or not isinstance(multiplier, int | float) or multiplier <= 0:
        raise ScoringConfigError(
            "competitor_metrics.outlier_multiplier must be a positive number in scoring.yaml,"
            f" got {multiplier!r}"
        )
    window = section.get("outlier_window_videos")
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ScoringConfigError(
            "competitor_metrics.outlier_window_videos must be a positive integer in"
            f" scoring.yaml, got {window!r}"
        )
    raw = section.get("length_buckets")
    if not isinstance(raw, list) or not raw:
        raise ScoringConfigError("competitor_metrics.length_buckets must be a non-empty list")
    buckets: list[LengthBucket] = []
    previous = -1
    for i, item in enumerate(raw):
        label = item.get("label") if isinstance(item, dict) else None
        top = item.get("max_seconds") if isinstance(item, dict) else None
        last = i == len(raw) - 1
        ok_top = top is None if last else isinstance(top, int) and not isinstance(top, bool)
        if (
            not isinstance(label, str)
            or not label
            or not ok_top
            or (top is not None and top <= previous)
        ):
            raise ScoringConfigError(
                "competitor_metrics.length_buckets: each item needs a label and a rising"
                f" integer max_seconds, and only the last has max_seconds: null (item {i})"
            )
        previous = top if top is not None else previous
        buckets.append(LengthBucket(label, top))
    return MetricsConfig(float(multiplier), window, tuple(buckets))
