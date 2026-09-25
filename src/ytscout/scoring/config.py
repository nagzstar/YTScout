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
    topic_words: tuple[str, ...]
    language: str
    latin_share_min: float


def discovery_config(config: dict[str, Any]) -> DiscoveryConfig:
    """The ``discovery:`` section: every key required; numbers non-negative, words
    non-empty."""
    section = config.get("discovery")
    if not isinstance(section, dict):
        raise ScoringConfigError("scoring.yaml has no `discovery:` mapping")
    values: dict[str, Any] = {}
    for f in fields(DiscoveryConfig):
        value = section.get(f.name)
        if f.type == "str":
            if not isinstance(value, str) or not value.strip():
                raise ScoringConfigError(f"discovery.{f.name} must be a word, got {value!r}")
            values[f.name] = value.strip().lower()
        elif f.type == "tuple[str, ...]":
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(w, str) and w.strip() for w in value)
            ):
                raise ScoringConfigError(
                    f"discovery.{f.name} must be a non-empty list of words, got {value!r}"
                )
            values[f.name] = tuple(w.strip().lower() for w in value)
        elif isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ScoringConfigError(
                f"discovery.{f.name} must be a non-negative number in scoring.yaml, got {value!r}"
            )
        else:
            values[f.name] = int(value) if f.type == "int" else float(value)
    if values["shorts_share_min"] > 1 or values["latin_share_min"] > 1:
        raise ScoringConfigError(
            "discovery.shorts_share_min and latin_share_min must be ≤ 1 in scoring.yaml"
        )
    if values["subs_max"] and values["subs_max"] < values["subs_min"]:
        raise ScoringConfigError("discovery.subs_max must be 0 (no cap) or ≥ subs_min")
    return DiscoveryConfig(**values)


@dataclass(frozen=True)
class ValidationConfig:
    """``niche_validation:`` in scoring.yaml: how ``scout validate`` samples a niche (022)."""

    small_subs_max: int
    small_age_days: int
    lookback_days: int
    max_channels: int
    early_stop_after_searches: int
    videos_per_channel: int
    language: str


def validation_config(config: dict[str, Any]) -> ValidationConfig:
    """The ``niche_validation:`` section: every key required, integers positive."""
    section = config.get("niche_validation")
    if not isinstance(section, dict):
        raise ScoringConfigError("scoring.yaml has no `niche_validation:` mapping")
    values: dict[str, Any] = {}
    for f in fields(ValidationConfig):
        value = section.get(f.name)
        if f.type == "str":
            if not isinstance(value, str) or not value.strip():
                raise ScoringConfigError(f"niche_validation.{f.name} must be a word, got {value!r}")
            values[f.name] = value.strip().lower()
        elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ScoringConfigError(
                f"niche_validation.{f.name} must be a positive integer in scoring.yaml,"
                f" got {value!r}"
            )
        else:
            values[f.name] = value
    if values["videos_per_channel"] > 50:
        raise ScoringConfigError(
            "niche_validation.videos_per_channel must be ≤ 50 (one playlist page)"
        )
    return ValidationConfig(**values)


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


# Keys of `niche_scoring:` (023) and how to validate them: (kind, minimum).
_NICHE_SCORING_NUMBERS: dict[str, tuple[str, float]] = {
    "small_subs_max": ("int", 1),
    "small_age_days": ("int", 1),
    "outlier_multiplier": ("number", 0),
    "outlier_window_videos": ("int", 1),
    "window_days": ("int", 1),
    "top_n_concentration": ("int", 1),
    "low_confidence_min_small": ("int", 0),
    "low_confidence_cap": ("number", 0),
    "manual_hours_floor_per_month": ("number", 0),
}
_NICHE_SCORING_PER_FORMAT: tuple[str, ...] = ("outlier_floor_views", "videos_per_month")
_NICHE_SCORING_WEIGHTS: tuple[str, ...] = (
    "small_outlier_rate",
    "newcomer_view_share",
    "inverse_concentration",
)


def _niche_number(where: str, value: Any, kind: str, minimum: float) -> float:
    ok_type = isinstance(value, int) if kind == "int" else isinstance(value, int | float)
    if isinstance(value, bool) or not ok_type or value < minimum:
        noun = "an integer" if kind == "int" else "a number"
        raise ScoringConfigError(
            f"{where} must be {noun} ≥ {minimum:g} in scoring.yaml, got {value!r}"
        )
    return value


def niche_scoring_config(config: dict[str, Any]) -> dict[str, Any]:
    """The ``niche_scoring:`` section (023), validated, plus the top-level
    ``shorts_max_seconds`` the format split needs. This is the ``cfg`` mapping every
    function in ``scoring.opportunity`` takes."""
    section = config.get("niche_scoring")
    if not isinstance(section, dict):
        raise ScoringConfigError("scoring.yaml has no `niche_scoring:` mapping")
    cfg: dict[str, Any] = {"shorts_max_seconds": shorts_max_seconds(config)}
    for key, (kind, minimum) in _NICHE_SCORING_NUMBERS.items():
        cfg[key] = _niche_number(f"niche_scoring.{key}", section.get(key), kind, minimum)
    if cfg["outlier_multiplier"] <= 0 or cfg["manual_hours_floor_per_month"] <= 0:
        raise ScoringConfigError(
            "niche_scoring.outlier_multiplier and manual_hours_floor_per_month must be > 0"
        )
    if cfg["low_confidence_cap"] > 1:
        raise ScoringConfigError("niche_scoring.low_confidence_cap must be ≤ 1")
    for key in _NICHE_SCORING_PER_FORMAT:
        raw = section.get(key)
        if not isinstance(raw, dict) or set(raw) != {"shorts", "longform"}:
            raise ScoringConfigError(f"niche_scoring.{key} must map shorts and longform to numbers")
        cfg[key] = {
            fmt: _niche_number(f"niche_scoring.{key}.{fmt}", raw[fmt], "number", 0)
            for fmt in ("shorts", "longform")
        }
    raw_weights = section.get("weights")
    if not isinstance(raw_weights, dict) or set(raw_weights) != set(_NICHE_SCORING_WEIGHTS):
        raise ScoringConfigError(
            "niche_scoring.weights must map exactly "
            + ", ".join(_NICHE_SCORING_WEIGHTS)
            + " to numbers"
        )
    cfg["weights"] = {
        k: _niche_number(f"niche_scoring.weights.{k}", raw_weights[k], "number", 0)
        for k in _NICHE_SCORING_WEIGHTS
    }
    if abs(sum(cfg["weights"].values()) - 1.0) > 1e-6:
        raise ScoringConfigError("niche_scoring.weights must sum to 1")
    return cfg
