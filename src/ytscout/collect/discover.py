"""``discover``: candidate competitors from searches seeded by the own channel's titles.

DESIGN.md §4.3. Own channel (1 unit) → ``search.list`` ×2 per query (100 units each) →
``channels.list`` for every distinct hit channel (1 unit per 50) → channel-level screen →
``videos.list`` for the hits of channels still standing (1 unit per 50) → Shorts screen →
survivors written as ``role='competitor'`` with ``discovery_json``. Nagz approves or
rejects them later (007, 008); a ``rejected`` channel is never written again.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ytscout.collect.walk import Counts, store_video, to_int
from ytscout.scoring import DiscoveryConfig
from ytscout.store import now_utc, repo, utc_now
from ytscout.text import parse_keywords, query_terms, seed_queries, tokens
from ytscout.youtube import DataApi, QuotaExhausted, parse_duration
from ytscout.youtube.client import MAX_IDS_PER_CALL

OWN_TITLES = 50
OWN_PARTS = "statistics,brandingSettings"
SEARCH_ORDERS = ("viewCount", "date")
SEARCH_MAX_RESULTS = 50
LOOKBACK = timedelta(days=365)
DEFAULT_MAX_SEARCHES = 20
MAX_SEARCHES = 25


def worst_case_units(queries: int) -> int:
    """Units a run of ``queries`` queries can cost at most.

    Each query is 2 searches (200 units) returning at most 100 hits, so at most 2 more
    ``channels.list`` and 2 more ``videos.list`` batches; plus the one own-channel call.
    """
    searches = queries * len(SEARCH_ORDERS)
    return 1 + searches * 100 + 2 * searches * SEARCH_MAX_RESULTS // MAX_IDS_PER_CALL


def queries_within(max_units: int | None, max_queries: int) -> int:
    """``max_queries`` trimmed so the worst case fits ``max_units``: never stop mid-run."""
    if max_units is None:
        return max_queries
    n = max_queries
    while n > 0 and worst_case_units(n) > max_units:
        n -= 1
    return n


@dataclass
class Candidate:
    """One hit channel, as seen by the searches and then ``channels.list``."""

    channel_id: str
    video_ids: list[str] = field(default_factory=list)
    hit_titles: list[str] = field(default_factory=list)
    matched_queries: list[str] = field(default_factory=list)
    item: dict[str, Any] | None = None  # the channels.list item, once fetched

    @property
    def hit_count(self) -> int:
        return len(self.video_ids)

    @property
    def subs(self) -> int | None:
        stats = (self.item or {}).get("statistics") or {}
        return None if stats.get("hiddenSubscriberCount") else to_int(stats.get("subscriberCount"))


@dataclass
class Verdict:
    """Why a candidate was kept or dropped. ``reasons`` explain either outcome."""

    channel_id: str
    kept: bool
    reasons: list[str]
    hit_count: int = 0
    keyword_overlap: int = 0
    matched_queries: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """``hit_count × keyword_overlap``: the dashboard's ordering for candidates."""
        return self.hit_count * self.keyword_overlap

    def discovery_json(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasons": self.reasons,
            "hit_count": self.hit_count,
            "matched_queries": self.matched_queries,
        }


@dataclass
class DiscoveryResult:
    queries: list[str] = field(default_factory=list)
    searches: int = 0
    candidates: dict[str, Candidate] = field(default_factory=dict)
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    counts: Counts = field(default_factory=Counts)
    stopped: QuotaExhausted | None = None

    @property
    def kept(self) -> list[Verdict]:
        return sorted(
            (v for v in self.verdicts.values() if v.kept), key=lambda v: (-v.score, v.channel_id)
        )

    @property
    def dropped(self) -> list[Verdict]:
        return [v for v in self.verdicts.values() if not v.kept]


# --- the filter (pure) --------------------------------------------------------------------


def size_band(own_subs: int | None, cfg: DiscoveryConfig) -> tuple[float, float]:
    """Subscriber range a competitor must sit in; a small or unknown own count → fixed band."""
    if own_subs is None or own_subs < cfg.small_own_subs:
        return 0, cfg.small_band_max
    return own_subs / cfg.size_band_factor, own_subs * cfg.size_band_factor


def keyword_overlap(hit_titles: Sequence[str], terms: set[str]) -> list[str]:
    """The query words found in the channel's hit titles, sorted."""
    found = {word for title in hit_titles for word in tokens(title)}
    return sorted(found & terms)


def screen_channel(
    c: Candidate,
    *,
    own_channel_id: str,
    rejected: set[str],
    band: tuple[float, float],
    terms: set[str],
    cfg: DiscoveryConfig,
) -> Verdict:
    """The checks ``channels.list`` and the search titles can answer. ``kept`` means
    "still standing": the Shorts check (``screen_format``) comes after ``videos.list``."""
    words = keyword_overlap(c.hit_titles, terms)
    verdict = Verdict(
        c.channel_id,
        kept=False,
        reasons=[],
        hit_count=c.hit_count,
        keyword_overlap=len(words),
        matched_queries=list(c.matched_queries),
    )
    if c.channel_id == own_channel_id:
        verdict.reasons.append("own channel")
        return verdict
    if c.channel_id in rejected:
        verdict.reasons.append("rejected before")
        return verdict
    if c.item is None:
        verdict.reasons.append("not returned by channels.list")
        return verdict
    lo, hi = band
    subs = c.subs
    if subs is None:
        verdict.reasons.append("subscriber count hidden")
    elif not lo <= subs <= hi:
        verdict.reasons.append(f"subs {subs:,} outside [{lo:,.0f}, {hi:,.0f}]")
    else:
        verdict.reasons.append(f"subs {subs:,} within [{lo:,.0f}, {hi:,.0f}]")
    if len(words) < cfg.keyword_overlap_min:
        verdict.reasons.append(
            f"keyword overlap {len(words)} < {cfg.keyword_overlap_min} ({', '.join(words) or '-'})"
        )
    else:
        verdict.reasons.append(f"keyword overlap {len(words)}: {', '.join(words)}")
    verdict.kept = subs is not None and lo <= subs <= hi and len(words) >= cfg.keyword_overlap_min
    return verdict


def screen_format(
    verdict: Verdict, durations: Sequence[int], *, shorts_max_seconds: int, cfg: DiscoveryConfig
) -> Verdict:
    """Drop ``verdict`` unless enough of the channel's hits are Shorts. Mutates and returns."""
    if not durations:
        verdict.kept = False
        verdict.reasons.append("no hit durations from videos.list")
        return verdict
    share = sum(1 for d in durations if d <= shorts_max_seconds) / len(durations)
    ok = share >= cfg.shorts_share_min
    verdict.kept = verdict.kept and ok
    verdict.reasons.append(
        f"shorts share {share:.0%} {'>=' if ok else '<'} {cfg.shorts_share_min:.0%}"
        f" of {len(durations)} hit(s)"
    )
    return verdict


# --- the run ------------------------------------------------------------------------------


def _batches(ids: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(ids), MAX_IDS_PER_CALL):
        yield ids[start : start + MAX_IDS_PER_CALL]


def _collect_hits(result: DiscoveryResult, query: str, response: Mapping[str, Any]) -> None:
    for item in response.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        channel_id = snippet.get("channelId")
        if not video_id or not channel_id:
            continue
        c = result.candidates.setdefault(channel_id, Candidate(channel_id))
        if query not in c.matched_queries:
            c.matched_queries.append(query)
        if video_id not in c.video_ids:
            c.video_ids.append(video_id)
            c.hit_titles.append(snippet.get("title") or "")


def discover(
    api: DataApi,
    conn: sqlite3.Connection,
    own_channel_id: str,
    *,
    titles: Sequence[str],
    rejected: set[str],
    own_subs_fallback: int | None,
    max_queries: int,
    cfg: DiscoveryConfig,
    shorts_max_seconds: int,
) -> DiscoveryResult:
    """Run discovery and write the survivors. A quota stop lands in ``result.stopped``;
    only channels fully judged before it are written."""
    result = DiscoveryResult()
    own_subs = own_subs_fallback
    keywords: list[str] = []
    videos: dict[str, dict[str, Any]] = {}
    try:
        own = api.channels([own_channel_id], part=OWN_PARTS).get("items", [])
        if own:
            own_subs = Candidate(own_channel_id, item=own[0]).subs or own_subs
            branding = (own[0].get("brandingSettings") or {}).get("channel") or {}
            keywords = parse_keywords(branding.get("keywords"))
        result.queries = seed_queries(titles, keywords, max_queries)

        published_after = utc_now() - LOOKBACK
        for query in result.queries:
            for order in SEARCH_ORDERS:
                response = api.search(
                    query,
                    order=order,
                    published_after=published_after,
                    max_results=SEARCH_MAX_RESULTS,
                )
                result.searches += 1
                _collect_hits(result, query, response)

        lookup = [cid for cid in result.candidates if cid != own_channel_id]
        for batch in _batches(lookup):
            for item in api.channels(batch).get("items", []):
                if item.get("id") in result.candidates:
                    result.candidates[item["id"]].item = item

        band = size_band(own_subs, cfg)
        terms = query_terms(result.queries)
        for c in result.candidates.values():
            result.verdicts[c.channel_id] = screen_channel(
                c,
                own_channel_id=own_channel_id,
                rejected=rejected,
                band=band,
                terms=terms,
                cfg=cfg,
            )
        standing = [v.channel_id for v in result.verdicts.values() if v.kept]
        hit_ids = [vid for cid in standing for vid in result.candidates[cid].video_ids]
        for batch in _batches(hit_ids):
            for item in api.videos(batch).get("items", []):
                videos[item["id"]] = item
    except QuotaExhausted as exc:
        result.stopped = exc
        return result

    for cid in standing:
        durations = []
        for vid in result.candidates[cid].video_ids:
            raw = ((videos.get(vid) or {}).get("contentDetails") or {}).get("duration")
            if raw:
                durations.append(parse_duration(raw))
        screen_format(
            result.verdicts[cid], durations, shorts_max_seconds=shorts_max_seconds, cfg=cfg
        )
    _write(conn, result, videos, shorts_max_seconds)
    return result


def _write(
    conn: sqlite3.Connection,
    result: DiscoveryResult,
    videos: Mapping[str, Mapping[str, Any]],
    shorts_max_seconds: int,
) -> None:
    """Upsert each survivor, its snapshot, its hit videos and their snapshots; one commit."""
    counts = result.counts
    with conn:
        for verdict in result.kept:
            c = result.candidates[verdict.channel_id]
            item = c.item or {}
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            related = (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
            repo.upsert_channel(
                conn,
                c.channel_id,
                role="competitor",
                title=snippet.get("title"),
                custom_url=snippet.get("customUrl"),
                country=snippet.get("country"),
                created_at=snippet.get("publishedAt"),
                uploads_playlist_id=related.get("uploads"),
                last_refreshed=now_utc(),
            )
            repo.set_channel_discovery(conn, c.channel_id, verdict.discovery_json())
            repo.add_channel_snapshot(
                conn,
                c.channel_id,
                subs=c.subs,
                view_count=to_int(stats.get("viewCount")),
                video_count=to_int(stats.get("videoCount")),
            )
            counts.channels += 1
            counts.channel_snapshots += 1
            for vid in c.video_ids:
                if vid in videos:
                    store_video(conn, videos[vid], c.channel_id, shorts_max_seconds)
                    counts.videos += 1
                    counts.video_snapshots += 1
