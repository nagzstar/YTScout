"""Render ``dashboard/index.html``: one self-contained file from SQLite, no network.

Everything the page needs is inlined at build time: the CSS and JS in ``base.html.j2``
and the vendored Chart.js from ``static/chart.umd.js``. The only external references the
page may carry are ``https://www.youtube.com/watch?v=`` links. Reads only; the DB is
never written here.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from ytscout.score import WINDOWS, load_videos
from ytscout.scoring.metrics import FORMATS, month_keys, monthly_views, top_bucket
from ytscout.store import repo
from ytscout.store.db import to_utc_iso, utc_now
from ytscout.youtube.quota import today_pacific

PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
CHARTJS_PATH = PACKAGE_DIR / "static" / "chart.umd.js"
DEFAULT_OUT_RELPATH = Path("dashboard") / "index.html"
OWN_VIDEOS_SHOWN = 20
CHART_MONTHS = 12
# At most 8 lines (the validated categorical palette); past that the smallest channels
# fold into one "Other" line.
CHART_SERIES = 8
WATCH_URL = "https://www.youtube.com/watch?v="

# A source map comment would make devtools try to fetch chart.umd.js.map next to the page.
_SOURCE_MAP = re.compile(r"^//# sourceMappingURL=.*$", re.MULTILINE)


@dataclass
class Dashboard:
    """Everything the templates read. Empty lists render as "nothing yet"."""

    built_at: str
    quota_today: int
    quota_yesterday: int
    own: dict[str, Any] | None = None
    last_run: dict[str, Any] | None = None
    subs_series: list[dict[str, Any]] = field(default_factory=list)
    own_videos: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    approved: list[dict[str, Any]] = field(default_factory=list)
    # {window: {format: [row per tracked channel, own first]}} from the latest metrics.
    metrics: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    metrics_at: str | None = None
    # {"labels": ["YYYY-MM", ...], "series": [{"name", "own", "data"}]}: views by publish month.
    monthly: dict[str, Any] = field(default_factory=dict)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _quota(conn: sqlite3.Connection, day: str) -> int:
    row = conn.execute("SELECT units_used FROM quota_ledger WHERE day_pacific = ?", (day,))
    found = row.fetchone()
    return int(found[0]) if found else 0


_LATEST_SUBS = (
    "(SELECT subs FROM channel_snapshots s WHERE s.channel_id = c.id"
    " ORDER BY s.captured_at DESC, s.id DESC LIMIT 1)"
)


def _discovery(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.pop("discovery_json", None)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _competitor(row: dict[str, Any]) -> dict[str, Any]:
    disc = _discovery(row)
    reasons = disc.get("reasons") or []
    row["score"] = disc.get("score")
    row["hit_count"] = disc.get("hit_count")
    row["reasons"] = [str(r) for r in reasons] if isinstance(reasons, list) else [str(reasons)]
    return row


def load(conn: sqlite3.Connection, now: datetime | None = None) -> Dashboard:
    """Read what the dashboard shows. ``now`` is an aware datetime (default: now)."""
    moment = utc_now() if now is None else now
    today = today_pacific(moment)
    dash = Dashboard(
        built_at=to_utc_iso(moment),
        quota_today=_quota(conn, today.isoformat()),
        quota_yesterday=_quota(conn, (today - timedelta(days=1)).isoformat()),
    )

    runs = _rows(
        conn,
        "SELECT kind, status, started_at, finished_at FROM runs"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
    )
    dash.last_run = runs[0] if runs else None

    own = _rows(
        conn,
        f"SELECT c.id, c.title, {_LATEST_SUBS} AS subs FROM channels c"
        " WHERE c.role = 'own' ORDER BY c.first_seen, c.id LIMIT 1",
    )
    if own:
        dash.own = own[0]
        own_id = dash.own["id"]
        dash.subs_series = _rows(
            conn,
            "SELECT captured_at, subs FROM channel_snapshots"
            " WHERE channel_id = ? AND subs IS NOT NULL ORDER BY captured_at, id",
            (own_id,),
        )
        dash.own_videos = _rows(
            conn,
            "SELECT v.id, v.title, v.published_at, v.duration_s, v.is_short,"
            " s.views, s.likes, s.comments"
            " FROM videos v LEFT JOIN video_snapshots s ON s.id = ("
            "   SELECT id FROM video_snapshots WHERE video_id = v.id"
            "   ORDER BY captured_at DESC, id DESC LIMIT 1)"
            " WHERE v.channel_id = ? ORDER BY v.published_at DESC, v.id LIMIT ?",
            (own_id, OWN_VIDEOS_SHOWN),
        )

    # Candidates: 006 left role='competitor', status NULL, discovery_json set.
    dash.candidates = [
        _competitor(r)
        for r in _rows(
            conn,
            f"SELECT c.id, c.title, c.discovery_json, {_LATEST_SUBS} AS subs FROM channels c"
            " WHERE c.role = 'competitor' AND c.status IS NULL",
        )
    ]
    dash.candidates.sort(key=lambda r: (-(r["score"] or 0), r["title"] or "", r["id"]))
    dash.approved = [
        _competitor(r)
        for r in _rows(
            conn,
            f"SELECT c.id, c.title, c.status, c.discovery_json, {_LATEST_SUBS} AS subs"
            " FROM channels c WHERE c.role = 'competitor' AND c.status IN ('approved', 'watch')"
            " ORDER BY c.status, c.title, c.id",
        )
    ]
    _load_metrics(conn, dash, moment)
    return dash


def _metric_row(channel: sqlite3.Row, own_id: str | None, m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": channel["id"],
        "title": channel["title"] or channel["id"],
        "own": channel["id"] == own_id,
        "video_count": m.get("video_count"),
        "uploads_per_week": m.get("uploads_per_week"),
        "views_median": m.get("views_median"),
        "views_p25": m.get("views_p25"),
        "views_p75": m.get("views_p75"),
        "views_per_sub": m.get("views_per_sub"),
        "outlier_count": m.get("outlier_count"),
        "top_bucket": top_bucket(m.get("length_buckets") or {}),
        "share": m.get("share_of_tracked_views"),
    }


def _load_metrics(conn: sqlite3.Connection, dash: Dashboard, now: datetime) -> None:
    """The side-by-side tables (latest ``channel_metrics``) and the monthly-views chart."""
    channels = repo.tracked_channels(conn)
    if not channels:
        return
    own_id = dash.own["id"] if dash.own else None
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in repo.latest_channel_metrics(conn):
        latest[(row["channel_id"], row["window"], row["format"])] = json.loads(row["metrics_json"])
        if dash.metrics_at is None or row["computed_at"] > dash.metrics_at:
            dash.metrics_at = row["computed_at"]
    if latest:
        dash.metrics = {
            window: {
                fmt: [
                    _metric_row(c, own_id, latest[(c["id"], window, fmt)])
                    for c in channels
                    if (c["id"], window, fmt) in latest
                ]
                for fmt in FORMATS
            }
            for window in WINDOWS
        }

    series = [
        {
            "name": c["title"] or c["id"],
            "own": c["id"] == own_id,
            "data": list(
                monthly_views(load_videos(conn, c["id"]), now=now, months=CHART_MONTHS).values()
            ),
        }
        for c in channels
    ]
    if not any(any(s["data"]) for s in series):
        return
    # Own first, then the biggest competitors; the tail folds into "Other".
    own = [s for s in series if s["own"]]
    rest = sorted((s for s in series if not s["own"]), key=lambda s: -sum(s["data"]))
    keep = len(rest) if len(own) + len(rest) <= CHART_SERIES else CHART_SERIES - 1 - len(own)
    shown = own + rest[:keep]
    folded = rest[keep:]
    if folded:
        shown.append(
            {
                "name": f"Other ({len(folded)} channels)",
                "own": False,
                "other": True,
                "data": [sum(col) for col in zip(*(s["data"] for s in folded), strict=True)],
            }
        )
    dash.monthly = {"labels": month_keys(now, CHART_MONTHS), "series": shown}


# --- rendering -----------------------------------------------------------------------------


def _number(value: Any) -> str:
    return "–" if value is None else f"{value:,}"


def _duration(seconds: Any) -> str:
    if seconds is None:
        return "–"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _decimal(value: Any, places: int = 2) -> str:
    return "–" if value is None else f"{value:,.{places}f}"


def _percent(value: Any) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def _day(timestamp: Any) -> str:
    return "–" if not timestamp else str(timestamp)[:10]


def chartjs_source(path: Path = CHARTJS_PATH) -> str:
    """The vendored Chart.js, ready to sit inside a ``<script>`` block."""
    source = _SOURCE_MAP.sub("", path.read_text(encoding="utf-8"))
    if "</script" in source.lower():
        raise ValueError(f"{path} contains '</script' and cannot be inlined")
    return source


def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["number"] = _number
    env.filters["duration"] = _duration
    env.filters["day"] = _day
    env.filters["decimal"] = _decimal
    env.filters["percent"] = _percent
    return env


def render(dash: Dashboard) -> str:
    template = environment().get_template("base.html.j2")
    return template.render(d=dash, watch_url=WATCH_URL, chartjs=chartjs_source())


def build(conn: sqlite3.Connection, out: Path, now: datetime | None = None) -> Dashboard:
    """Render the dashboard from ``conn`` into ``out`` (parents created); return its data."""
    dash = load(conn, now)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(dash), encoding="utf-8", newline="\n")
    return dash
