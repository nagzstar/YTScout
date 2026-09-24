"""YouTube Data API v3 access: the quota ledger, swappable transports and the client.

Every call goes ``DataApi`` → ``Ledger.charge`` → ``Transport.call``. The Google client library is
imported only in ``transport.py``; tests use ``FakeTransport`` and ``--dry-run`` uses
``DryRunTransport``, so nothing but ``GoogleTransport`` touches the network.
"""

from ytscout.youtube.client import DataApi, parse_dt, parse_duration
from ytscout.youtube.quota import UNIT_COSTS, Ledger, QuotaExhausted, today_pacific
from ytscout.youtube.transport import (
    DryRunTransport,
    FakeTransport,
    GoogleTransport,
    NotModified,
    Transport,
)

__all__ = [
    "UNIT_COSTS",
    "DataApi",
    "DryRunTransport",
    "FakeTransport",
    "GoogleTransport",
    "Ledger",
    "NotModified",
    "QuotaExhausted",
    "Transport",
    "parse_dt",
    "parse_duration",
    "today_pacific",
]
