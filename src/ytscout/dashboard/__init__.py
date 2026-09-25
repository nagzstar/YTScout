"""The static dashboard (007): ``build`` renders one self-contained HTML file."""

from ytscout.dashboard.build import (
    DEFAULT_OUT_RELPATH,
    Dashboard,
    NicheContext,
    build,
    load,
    niche_context,
    render,
)

__all__ = [
    "DEFAULT_OUT_RELPATH",
    "Dashboard",
    "NicheContext",
    "build",
    "load",
    "niche_context",
    "render",
]
