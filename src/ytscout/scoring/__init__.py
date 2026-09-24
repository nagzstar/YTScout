"""Scoring: pure functions over plain dataclasses and config dicts. Never imports the DB."""

from ytscout.scoring.config import (
    SCORING_RELPATH,
    DiscoveryConfig,
    ScoringConfigError,
    discovery_config,
    load_scoring,
    shorts_max_seconds,
)

__all__ = [
    "SCORING_RELPATH",
    "DiscoveryConfig",
    "ScoringConfigError",
    "discovery_config",
    "load_scoring",
    "shorts_max_seconds",
]
