"""Render ``dashboard/index.html``: one self-contained file from SQLite, no network.

Everything the page needs is inlined at build time: the CSS and JS in ``base.html.j2``
and the vendored Chart.js from ``static/chart.umd.js``. The only external references the
page may carry are ``https://www.youtube.com/watch?v=`` and ``/channel/`` links. Reads only;
the DB is never written here.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from ytscout.audit import COVERAGE_RELPATH, AuditError, PipelineCoverage, load_coverage
from ytscout.score import WINDOWS, load_videos
from ytscout.scoring import opportunity
from ytscout.scoring.config import (
    SCORING_RELPATH,
    ScoringConfigError,
    load_scoring,
    niche_scoring_config,
)
from ytscout.scoring.metrics import FORMATS, month_keys, monthly_views, top_bucket
from ytscout.scout.score import build_sample
from ytscout.store import repo
from ytscout.store.db import to_utc_iso, utc_now
from ytscout.youtube import parse_dt
from ytscout.youtube.quota import today_pacific

PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
CHARTJS_PATH = PACKAGE_DIR / "static" / "chart.umd.js"
DEFAULT_OUT_RELPATH = Path("dashboard") / "index.html"
OWN_VIDEOS_SHOWN = 20
RUNS_SHOWN = 10
UNITS_CHART_DAYS = 14
STALE_AFTER_DAYS = 8
# run_weekly.ps1 names its log weekly-YYYYMMDD-HHMM.log; runs rows carrying such a path
# are the weekly job's steps.
WEEKLY_LOG_MARK = "weekly-"
CHART_MONTHS = 12
# At most 8 lines (the validated categorical palette); past that the smallest channels
# fold into one "Other" line.
CHART_SERIES = 8
WATCH_URL = "https://www.youtube.com/watch?v="
CHANNEL_URL = "https://www.youtube.com/channel/"

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
    rejected: list[dict[str, Any]] = field(default_factory=list)
    # {window: {format: [row per tracked channel, own first]}} from the latest metrics.
    metrics: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    metrics_at: str | None = None
    # {"labels": ["YYYY-MM", ...], "series": [{"name", "own", "data"}]}: views by publish month.
    monthly: dict[str, Any] = field(default_factory=dict)
    # The latest competitor analysis (017): status, run_at, hashes, the grounded analysis
    # and the id → title maps the Findings section links with. None when never run.
    findings: dict[str, Any] | None = None
    # {format: [latest niche_scores row per niche, best score first]} (025).
    niches: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Niches with no niche_scores row yet (proposed, validated): the "waiting" table.
    niches_waiting: list[dict[str, Any]] = field(default_factory=list)
    niche_counts: dict[str, int] = field(default_factory=dict)
    # The Runs section (031): recent rows, this week's totals, units by day, pending
    # analyses, the health banner's reasons and the latest weekly log path.
    runs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NicheContext:
    """What a niche row's sample needs beyond the DB: ``niche_scoring`` (small channels,
    outliers) and the pipeline coverage. Either may be ``None``; that part is left out."""

    cfg: dict[str, Any] | None = None
    coverage: PipelineCoverage | None = None


def niche_context(root: Path) -> NicheContext:
    """Read ``config/scoring.yaml`` and ``config/pipeline_coverage.yaml`` under ``root``. A
    missing or invalid file drops only its part of the sample, never the build."""
    cfg: dict[str, Any] | None = None
    coverage: PipelineCoverage | None = None
    try:
        cfg = niche_scoring_config(load_scoring(root / SCORING_RELPATH))
    except (OSError, ScoringConfigError):
        pass
    try:
        coverage = load_coverage(root / COVERAGE_RELPATH)
    except (OSError, AuditError):
        pass
    return NicheContext(cfg, coverage)


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


def load(
    conn: sqlite3.Connection, now: datetime | None = None, context: NicheContext | None = None
) -> Dashboard:
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
            f"SELECT c.id, c.title, c.status, c.discovery_json, {_LATEST_SUBS} AS subs"
            " FROM channels c WHERE c.role = 'competitor' AND c.status IS NULL",
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
    # Rejected (035): kept on the page, collapsed, so a mis-click can be undone.
    dash.rejected = [
        _competitor(r)
        for r in _rows(
            conn,
            f"SELECT c.id, c.title, c.status, c.discovery_json, {_LATEST_SUBS} AS subs"
            " FROM channels c WHERE c.role = 'competitor' AND c.status = 'rejected'"
            " ORDER BY c.title, c.id",
        )
    ]
    _load_metrics(conn, dash, moment)
    _load_findings(conn, dash)
    _load_niches(conn, dash, context or NicheContext())
    dash.runs = load_runs(conn, moment)
    return dash


_ID_LIST_SUFFIXES = ("_video_ids",)


def _cited_video_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith(_ID_LIST_SUFFIXES) and isinstance(item, list):
                ids.update(str(v) for v in item)
            else:
                ids |= _cited_video_ids(item)
    elif isinstance(value, list):
        for item in value:
            ids |= _cited_video_ids(item)
    return ids


def _load_findings(conn: sqlite3.Connection, dash: Dashboard) -> None:
    """The latest ``competitor_analyses`` row, with titles for every id it cites."""
    row = repo.latest_competitor_analysis(conn)
    if row is None:
        return
    result = json.loads(row["result_json"]) if row["result_json"] else {}
    findings: dict[str, Any] = {
        "status": row["status"],
        "run_at": row["run_at"],
        "prompt_hash": row["prompt_hash"],
        "schema_hash": row["schema_version"],
        "error": result.get("error") if row["status"] != "ok" else None,
        "analysis": None,
        "titles": {},
        "channel_titles": {},
    }
    if row["status"] == "ok":
        findings["analysis"] = result
        findings["titles"] = repo.video_titles(conn, _cited_video_ids(result))
        channel_ids = {c["channel_id"] for c in result.get("per_competitor", [])}
        for gap in result.get("topic_gaps", []):
            channel_ids.update(gap.get("covered_by_channel_ids", []))
        titles: dict[str, str | None] = {}
        for channel_id in channel_ids:
            channel = repo.get_channel(conn, channel_id)
            titles[channel_id] = channel["title"] if channel else None
        findings["channel_titles"] = titles
    dash.findings = findings


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


# --- niches (025) ----------------------------------------------------------------------------

TREND_MIN_DAYS = 28
TREND_STEP = 0.05
# `track`/`shelve` are the stored decision values; DESIGN.md §5.4 names them tracking/shelved.
NICHE_TRACKING = ("track", "tracking")
NICHE_SHELVED = ("shelve", "shelved")


def _json_list(raw: Any) -> list[str]:
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def trend(history: list[dict[str, Any]]) -> str:
    """▲/▼/▬ from the latest ``opportunity`` against the newest row at least 28 days older
    than it (±0.05); "—" when there is no such row. ``history`` is oldest first."""
    latest = history[-1]
    cutoff = parse_dt(latest["scored_at"]) - timedelta(days=TREND_MIN_DAYS)
    older = [r for r in history[:-1] if parse_dt(r["scored_at"]) <= cutoff]
    if not older or latest["opportunity"] is None or older[-1]["opportunity"] is None:
        return "—"
    delta = round(latest["opportunity"] - older[-1]["opportunity"], 9)
    if delta >= TREND_STEP:
        return "▲"
    if delta <= -TREND_STEP:
        return "▼"
    return "▬"


def _niche_sample(
    conn: sqlite3.Connection, niche: dict[str, Any], scored_at: str, context: NicheContext
) -> dict[str, Any]:
    """The expanded row: small channels and their outliers (as of the score), the queries,
    the required steps and what the pipeline covers of each."""
    coverage = context.coverage
    sample: dict[str, Any] = {
        "queries": _json_list(niche["queries_json"]),
        "steps": [
            {
                "id": step,
                "coverage": coverage.steps[step].coverage
                if coverage is not None and step in coverage.steps
                else None,
            }
            for step in _json_list(niche["required_steps_json"])
        ],
        "coverage_known": coverage is not None,
        "channels": None,
    }
    if context.cfg is None:
        return sample
    cfg = context.cfg
    ns = build_sample(conn, niche["id"], cfg, now=parse_dt(scored_at))
    channels = []
    for ch in opportunity.small_channels(ns, cfg):
        row = repo.get_channel(conn, ch.channel_id)
        outliers = sorted(opportunity.outlier_videos(ch, ns, cfg), key=lambda v: -v.views)
        channels.append(
            {
                "id": ch.channel_id,
                "title": (row["title"] if row else None) or ch.channel_id,
                "subs": ch.subs,
                "outliers": [{"id": v.video_id, "views": v.views} for v in outliers],
            }
        )
    titles = repo.video_titles(conn, (v["id"] for c in channels for v in c["outliers"]))
    for c in channels:
        for v in c["outliers"]:
            v["title"] = titles.get(v["id"]) or v["id"]
    sample["channels"] = channels
    return sample


def _gbp_band(latest: dict[str, Any], views: float | None) -> float | None:
    """A views quantile in £/month: est = p50 views / 1000 × rpm_gbp, so £ scales with views."""
    if views is None or latest["rpm_gbp"] is None:
        return None
    return views / 1000.0 * latest["rpm_gbp"]


def _load_niches(conn: sqlite3.Connection, dash: Dashboard, context: NicheContext) -> None:
    """Latest ``niche_scores`` row per niche, split by format and ranked by score; niches
    without a score go to the waiting list."""
    niches = _rows(conn, "SELECT * FROM niches ORDER BY id")
    if not niches:
        return
    history: dict[int, list[dict[str, Any]]] = {}
    for row in _rows(conn, "SELECT * FROM niche_scores ORDER BY scored_at, id"):
        history.setdefault(row["niche_id"], []).append(row)
    tables: dict[str, list[dict[str, Any]]] = {fmt: [] for fmt in FORMATS}
    for niche in niches:
        rows = history.get(niche["id"])
        if not rows:
            dash.niches_waiting.append(niche)
            continue
        latest = rows[-1]
        tables.setdefault(niche["format"], []).append(
            {
                "id": niche["id"],
                "label": niche["label"] or niche["topic"],
                "category": niche["topic_category"],
                "status": niche["status"],
                "scored_at": latest["scored_at"],
                "score": latest["score"],
                "opportunity": latest["opportunity"],
                "est_p50": latest["est_monthly_gbp"],
                "est_p25": _gbp_band(latest, latest["newcomer_monthly_views_p25"]),
                "est_p75": _gbp_band(latest, latest["newcomer_monthly_views_p75"]),
                "hours": latest["manual_hours_per_month"],
                "flags": _json_list(latest["confidence_flags_json"]),
                "trend": trend(rows),
                "sample": _niche_sample(conn, niche, latest["scored_at"], context),
            }
        )
    for ranked in tables.values():
        ranked.sort(key=lambda r: (-(r["score"] or 0.0), r["id"]))
    dash.niches = tables
    dash.niche_counts = {
        "scored": sum(len(r) for r in tables.values()),
        "tracking": sum(1 for n in niches if n["status"] in NICHE_TRACKING),
        "shelved": sum(1 for n in niches if n["status"] in NICHE_SHELVED),
    }


# --- runs (031) ----------------------------------------------------------------------------

_RUN_COLUMNS = (
    "id, kind, started_at, finished_at, status, log_path, units_used, claude_calls,"
    " claude_input_tokens, claude_output_tokens, claude_cost_usd_est, error_tail"
)
_TOTALS = ("units_used", "claude_calls", "claude_input_tokens", "claude_output_tokens")


def _parse(timestamp: Any) -> datetime | None:
    try:
        return parse_dt(str(timestamp)) if timestamp else None
    except ValueError:
        return None


def _seconds(row: dict[str, Any]) -> float | None:
    start, end = _parse(row.get("started_at")), _parse(row.get("finished_at"))
    return None if start is None or end is None else max(0.0, (end - start).total_seconds())


def _sum(rows: list[dict[str, Any]], key: str) -> int | float | None:
    """Sum of the non-null ``key`` values; ``None`` when every row is null."""
    values = [r[key] for r in rows if r.get(key) is not None]
    return sum(values) if values else None


def _is_weekly(row: dict[str, Any]) -> bool:
    return WEEKLY_LOG_MARK in Path(str(row.get("log_path") or "")).name


def week_start(now: datetime) -> datetime:
    """Monday 00:00 UTC of the week containing ``now``."""
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def run_health(rows: list[dict[str, Any]], now: datetime) -> list[str]:
    """Why the Runs banner is red; empty when the job looks healthy.

    ``rows`` are all runs, newest first. The newest *weekly* run is every row sharing the
    newest weekly log path (one per step of run_weekly.ps1); with no weekly rows at all,
    the newest run of any kind stands in. Red when any of those rows is not ``ok`` or when
    it started more than ``STALE_AFTER_DAYS`` ago (or nothing ever ran).
    """
    weekly = [r for r in rows if _is_weekly(r)]
    if weekly:
        latest = [r for r in weekly if r["log_path"] == weekly[0]["log_path"]]
        label = "the newest weekly run"
    else:
        latest = rows[:1]
        label = "the newest run"
    reasons: list[str] = []
    bad = [r for r in latest if r.get("status") != "ok"]
    if bad:
        steps = ", ".join(f"{r['kind']} {r.get('status') or 'unfinished'}" for r in bad)
        reasons.append(f"{label} did not finish ok: {steps}")
    started = [t for t in (_parse(r.get("started_at")) for r in latest) if t is not None]
    if not started or now - max(started) > timedelta(days=STALE_AFTER_DAYS):
        reasons.append(f"no run in the last {STALE_AFTER_DAYS} days")
    return reasons


def pending_analyses(conn: sqlite3.Connection) -> int:
    """Competitor analyses stored ``pending`` (claude unavailable) since the last ok one."""
    row = conn.execute(
        "SELECT COUNT(*) FROM competitor_analyses WHERE status = 'pending' AND id >"
        " COALESCE((SELECT MAX(id) FROM competitor_analyses WHERE status = 'ok'), 0)"
    ).fetchone()
    return int(row[0])


def units_by_day(conn: sqlite3.Connection, now: datetime) -> dict[str, list[Any]]:
    """Ledger units per Pacific day, oldest first, ``UNITS_CHART_DAYS`` days ending today."""
    today = today_pacific(now)
    days = [(today - timedelta(days=n)).isoformat() for n in range(UNITS_CHART_DAYS - 1, -1, -1)]
    used = dict(
        conn.execute(
            "SELECT day_pacific, units_used FROM quota_ledger WHERE day_pacific >= ?",
            (days[0],),
        ).fetchall()
    )
    return {"labels": days, "units": [int(used.get(d, 0)) for d in days]}


def load_runs(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """Everything the Runs section shows."""
    rows = _rows(conn, f"SELECT {_RUN_COLUMNS} FROM runs ORDER BY started_at DESC, id DESC")
    for row in rows:
        row["duration_s"] = _seconds(row)
        tokens = [row["claude_input_tokens"], row["claude_output_tokens"]]
        row["tokens"] = None if tokens == [None, None] else sum(t or 0 for t in tokens)
    since = week_start(now)
    week = [r for r in rows if (t := _parse(r["started_at"])) is not None and t >= since]
    totals: dict[str, Any] = {key: _sum(week, key) for key in _TOTALS}
    totals["claude_cost_usd_est"] = _sum(week, "claude_cost_usd_est")
    totals["runs"] = len(week)
    totals["not_ok"] = sum(1 for r in week if r.get("finished_at") and r.get("status") != "ok")
    totals["since"] = to_utc_iso(since)
    log = next((r["log_path"] for r in rows if r.get("log_path")), None)
    return {
        "recent": rows[:RUNS_SHOWN],
        "week": totals,
        "units_by_day": units_by_day(conn, now),
        "pending": pending_analyses(conn),
        "banner": run_health(rows, now),
        "log_path": log,
    }


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


def _sig2(value: Any) -> str:
    """Two significant figures: 0.0578 → "0.058", 1.04 → "1.0", 13,300 → "13,000"."""
    if value is None:
        return "–"
    number = float(value)
    if number == 0 or not math.isfinite(number):
        return f"{number:g}"
    rounded = float(f"{number:.2g}")
    if abs(rounded) >= 10:
        return f"{rounded:,.0f}"
    places = max(0, 1 - math.floor(math.log10(abs(rounded))))
    return f"{rounded:.{places}f}"


def _full(value: Any) -> str:
    """The tooltip behind a 2-significant-figure cell."""
    return "" if value is None else f"{float(value):.6g}"


def _percent(value: Any) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def _day(timestamp: Any) -> str:
    return "–" if not timestamp else str(timestamp)[:10]


def _clock(timestamp: Any) -> str:
    """``HH:MM UTC`` from an ISO timestamp (the pending-analysis note)."""
    text = str(timestamp or "")
    return f"{text[11:16]} UTC" if len(text) >= 16 and text[10] == "T" else (text or "–")


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
    env.filters["clock"] = _clock
    env.filters["decimal"] = _decimal
    env.filters["percent"] = _percent
    env.filters["sig2"] = _sig2
    env.filters["full"] = _full
    return env


def render(dash: Dashboard) -> str:
    template = environment().get_template("base.html.j2")
    return template.render(
        d=dash, watch_url=WATCH_URL, channel_url=CHANNEL_URL, chartjs=chartjs_source()
    )


def build(
    conn: sqlite3.Connection,
    out: Path,
    now: datetime | None = None,
    context: NicheContext | None = None,
) -> Dashboard:
    """Render the dashboard from ``conn`` into ``out`` (parents created); return its data."""
    dash = load(conn, now, context)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(dash), encoding="utf-8", newline="\n")
    return dash
