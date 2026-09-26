"""``score --niches``: build each tagged niche's sample from the DB, run the pure scorers
in ``ytscout.scoring`` (DESIGN.md §6), append a ``niche_scores`` row.

The DB side of issue 023's model: ``build_sample`` turns ``niche_channels`` + the latest
snapshots + the channels' videos into a ``NicheSample``; ``calibration_from_db`` turns the
own channel's Analytics rows into the RPM calibration. Everything else is passed in, so a
test can hand over the worked example's RPM table and coverage.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ytscout.audit import PipelineCoverage, Step
from ytscout.scoring import RelevanceConfig, effort, final, money, opportunity
from ytscout.scoring.types import ChannelSample, NicheSample, VideoSample
from ytscout.scout import relevance as relevance_gate
from ytscout.store import repo, to_utc_iso
from ytscout.youtube import parse_dt

CALIBRATION_DAYS = 90
CALIBRATION_MIN_VIEWS = 1000
LONGFORM_UNCALIBRATED = "longform_uncalibrated"
NO_SMALL_CHANNELS = "no_small_channels"
# Small channels exist but none published inside the window (051): no newcomer views.
NO_ACTIVE_SMALL_CHANNELS = "no_active_small_channels"


def _dt(value: str | None) -> datetime | None:
    return parse_dt(value) if value else None


def build_sample(
    conn: sqlite3.Connection, niche_id: int, cfg: Mapping[str, Any], *, now: datetime
) -> NicheSample:
    """Every channel linked to the niche and not excluded by the relevance gate (049), with
    its latest subscriber count, creation date and every video that has a publish date and
    a snapshot (latest views).

    ``cfg`` is ``scoring.niche_scoring_config``; the scorers do the window and format
    filtering themselves, so all videos are loaded.
    """
    niche = repo.get_niche(conn, niche_id)
    if niche is None:
        raise LookupError(f"no niche {niche_id!r}")
    by_channel: dict[str, list[VideoSample]] = {}
    for row in repo.niche_sample_videos(conn, niche_id):
        if row["views"] is None or not row["published_at"]:
            continue
        by_channel.setdefault(row["channel_id"], []).append(
            VideoSample(
                views=row["views"],
                published_at=parse_dt(row["published_at"]),
                duration_s=row["duration_s"],
                video_id=row["id"],
            )
        )
    channels = []
    for link in repo.niche_channels(conn, niche_id):
        if link["excluded_reason"] is not None:
            continue
        cid = link["channel_id"]
        snap = repo.latest_channel_snapshot(conn, cid)
        channel = repo.get_channel(conn, cid)
        channels.append(
            ChannelSample(
                channel_id=cid,
                subs=snap["subs"] if snap else None,
                created_at=_dt(channel["created_at"]) if channel else None,
                videos=by_channel.get(cid, []),
            )
        )
    return NicheSample(channels=channels, fmt=niche["format"], now=now)


def own_rpm_usd(
    conn: sqlite3.Connection, *, now: datetime, shorts_max_seconds: int
) -> float | None:
    """Median ``rpm_usd`` over own Shorts with ≥ 1,000 views, from each video's newest
    Analytics window (widest when two end the same day) ending within the last 90 days.
    A video with no ``videos`` row has no known format and is left out. ``None`` when
    nothing qualifies."""
    since = (now - timedelta(days=CALIBRATION_DAYS)).date().isoformat()
    seen: set[str] = set()
    rpms: list[float] = []
    for row in repo.own_rpm_rows(conn, since):
        if row["video_id"] in seen:
            continue
        seen.add(row["video_id"])
        if row["duration_s"] is None or row["duration_s"] > shorts_max_seconds:
            continue
        if row["rpm_usd"] is None or (row["views"] or 0) < CALIBRATION_MIN_VIEWS:
            continue
        rpms.append(row["rpm_usd"])
    return statistics.median(rpms) if rpms else None


def calibration_from_db(
    conn: sqlite3.Connection,
    table: money.RpmTableLike,
    *,
    now: datetime,
    shorts_max_seconds: int,
) -> float | None:
    """Own actual RPM ÷ the table's reference row (§6.3), or ``None`` when the own channel
    has no qualifying Analytics rows (the caller uses 1.0 and flags ``uncalibrated``)."""
    own = own_rpm_usd(conn, now=now, shorts_max_seconds=shorts_max_seconds)
    if own is None or own <= 0:
        return None
    return money.calibration(own, table)


@dataclass(frozen=True)
class NicheScore:
    niche_id: int
    label: str
    format: str
    values: dict[str, float | None]
    flags: list[str]
    # Channels in niche_channels, and how many of them the relevance gate left out (049).
    sampled: int = 0
    excluded: int = 0

    @property
    def score(self) -> float:
        return self.values["score"] or 0.0


def _required_steps(niche: sqlite3.Row) -> list[str]:
    value = json.loads(niche["required_steps_json"] or "[]")
    return [str(s) for s in value] if isinstance(value, list) else []


def score_niche(
    conn: sqlite3.Connection,
    niche: sqlite3.Row,
    *,
    cfg: Mapping[str, Any],
    rpm: money.RpmTableLike,
    steps: Iterable[Step],
    coverage: PipelineCoverage,
    usd_gbp: float,
    calibration: float | None,
    now: datetime,
) -> NicheScore:
    """One niche through §6.1-6.5. ``calibration`` is ``calibration_from_db``'s answer."""
    fmt = niche["format"]
    links = repo.niche_channels(conn, niche["id"])
    sample = build_sample(conn, niche["id"], cfg, now=now)
    opp, flags = opportunity.opportunity(sample, cfg)
    flags = list(flags)
    views = opportunity.newcomer_monthly_views(sample, cfg)
    if not opportunity.small_channels(sample, cfg):
        flags.append(NO_SMALL_CHANNELS)
    elif views is None:
        flags.append(NO_ACTIVE_SMALL_CHANNELS)
    p25, p50, p75 = views if views is not None else (None, None, None)

    if fmt == "longform":
        cal = 1.0
        flags.append(LONGFORM_UNCALIBRATED)
    elif calibration is None:
        cal = 1.0
        flags.append(money.UNCALIBRATED)
    else:
        cal = calibration
    rpm_gbp = money.rpm_gbp(niche["topic_category"], fmt, rpm, cal, usd_gbp)
    anchor = opportunity.newcomer_anchor_views(sample, cfg)
    est = money.est_monthly_gbp(anchor or 0.0, rpm_gbp)

    per_video, effort_flags = effort.manual_hours_per_video(
        _required_steps(niche),
        steps,
        coverage,
        fmt,
        needs_specific_footage=bool(niche["needs_specific_footage"]),
    )
    flags.extend(effort_flags)
    per_month = effort.manual_hours_per_month(per_video, cfg["videos_per_month"][fmt])
    value = final.score(est, per_month, cfg["manual_hours_floor_per_month"])
    return NicheScore(
        niche_id=niche["id"],
        label=niche["label"] or niche["topic"],
        format=fmt,
        values={
            "opportunity": opp,
            "small_outlier_rate": opportunity.small_outlier_rate(sample, cfg),
            "newcomer_view_share": opportunity.newcomer_view_share(sample, cfg),
            "concentration": opportunity.concentration(sample, cfg),
            "newcomer_monthly_views_p25": p25,
            "newcomer_monthly_views_p50": p50,
            "newcomer_monthly_views_p75": p75,
            "rpm_gbp": rpm_gbp,
            "est_monthly_gbp": est,
            # inf is not valid JSON and SQLite stores it as a REAL Infinity; NULL + the
            # `disqualified` flag says the same thing without surprising a reader.
            "manual_hours_per_month": None if math.isinf(per_month) else per_month,
            "score": value,
        },
        flags=flags,
        sampled=len(links),
        excluded=sum(1 for link in links if link["excluded_reason"] is not None),
    )


@dataclass
class NicheScoreResult:
    scored: list[NicheScore] = field(default_factory=list)
    skipped_untagged: int = 0
    scored_at: str = ""

    def ranked(self) -> list[NicheScore]:
        return sorted(self.scored, key=lambda s: (-s.score, s.niche_id))


def score_niches(
    conn: sqlite3.Connection,
    *,
    cfg: Mapping[str, Any],
    rpm: money.RpmTableLike,
    steps: Iterable[Step],
    coverage: PipelineCoverage,
    usd_gbp: float,
    now: datetime,
    niche_ids: Collection[int] | None = None,
    relevance: RelevanceConfig | None = None,
) -> NicheScoreResult:
    """Score every tagged validated/scored/tracked niche (only ``niche_ids`` when given);
    append one row each, in one transaction; ``validated`` becomes ``scored``. Untagged
    niches are skipped and counted.

    With ``relevance``, the relevance gate first re-screens every sampled niche's channels
    (shelved niches too, for the dashboard), in the same transaction. Without it the stored
    verdicts stand."""
    steps = list(steps)
    result = NicheScoreResult(scored_at=to_utc_iso(now))
    calibration = calibration_from_db(
        conn, rpm, now=now, shorts_max_seconds=int(cfg["shorts_max_seconds"])
    )
    with conn:
        if relevance is not None:
            relevance_gate.refresh_all(conn, relevance, niche_ids)
        for niche in repo.scorable_niches(conn):
            if niche_ids is not None and niche["id"] not in niche_ids:
                continue
            if niche["tag_prompt_hash"] is None:
                result.skipped_untagged += 1
                continue
            scored = score_niche(
                conn,
                niche,
                cfg=cfg,
                rpm=rpm,
                steps=steps,
                coverage=coverage,
                usd_gbp=usd_gbp,
                calibration=calibration,
                now=now,
            )
            repo.add_niche_score(
                conn, niche["id"], scored_at=now, flags=scored.flags, **scored.values
            )
            repo.mark_niche_scored(conn, niche["id"])
            result.scored.append(scored)
    return result


def _num(value: float | None, fmt: str) -> str:
    return "-" if value is None else format(value, fmt)


def format_table(result: NicheScoreResult) -> str:
    """The ranked table: best £ per manual hour first."""
    rows = [("#", "id", "label", "format", "kept", "score", "opp", "£/mo", "h/mo", "flags")]
    for i, s in enumerate(result.ranked(), 1):
        v = s.values
        rows.append(
            (
                str(i),
                str(s.niche_id),
                s.label[:48],
                s.format,
                f"{s.sampled - s.excluded}/{s.sampled}",
                _num(v["score"], ".3f"),
                _num(v["opportunity"], ".3f"),
                _num(v["est_monthly_gbp"], ",.2f"),
                _num(v["manual_hours_per_month"], ".1f")
                if v["manual_hours_per_month"] is not None
                else "inf",
                ",".join(s.flags) or "-",
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for n, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    if any(money.UNCALIBRATED in s.flags for s in result.scored):
        lines.append(f"note: {money.UNCALIBRATED_LABEL} (RPMs are the tier table's mid)")
    return "\n".join(lines)
