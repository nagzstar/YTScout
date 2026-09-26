"""Scoring: pure functions over plain dataclasses and config dicts. Never imports the DB."""

from ytscout.scoring.config import (
    SCORING_RELPATH,
    DiscoveryConfig,
    RelevanceConfig,
    ScoringConfigError,
    ValidationConfig,
    discovery_config,
    load_scoring,
    metrics_config,
    niche_scoring_config,
    relevance_config,
    shorts_max_seconds,
    summaries_per_channel,
    validation_config,
)

__all__ = [
    "SCORING_RELPATH",
    "DiscoveryConfig",
    "ScoringConfigError",
    "discovery_config",
    "load_scoring",
    "metrics_config",
    "niche_scoring_config",
    "RelevanceConfig",
    "relevance_config",
    "shorts_max_seconds",
    "summaries_per_channel",
    "validation_config",
    "ValidationConfig",
]
