"""``scout snowball``: adjacent niches from channels we already know (DESIGN.md §5.2 feed 2).

1. Sources, free from the DB: every ``approved`` competitor and every channel of a
   tracking niche; from each, its 3 most-viewed videos (latest snapshot).
2. One query per source title (``text.snowball_query``), taken round-robin by rank so no
   one source fills the list, de-duplicated, minus any query an existing niche already
   uses, capped at ``--max-searches``.
3. One ``search.list`` per query (``order=viewCount``, one year back). Hits on channels
   already in ``niche_channels`` or tracked (own, approved, watch) are ignored; channels
   with fewer than 3 hits across all queries are dropped before ``channels.list``.
4. Channels are clustered by their hit titles' content words: Jaccard ≥ 0.3, single
   linkage, clusters of fewer than 3 dropped. One ``videos.list`` pass over the clusters'
   hit videos gives durations.
5. Each cluster becomes a ``proposed`` niche with ``source='snowball'`` and its channel ids
   in ``seed_json``. Nothing is written before the last API call succeeds.

Worst case per run with N searches: ``N × 100 + ceil(⌊50N / 3⌋ / 50) + N`` — at most
``⌊50N / 3⌋`` channels can reach 3 hits and at most ``50N`` hit videos exist. N = 10 is
1,014 units; N = 15 is 1,520.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ytscout import text
from ytscout.collect.walk import batches
from ytscout.scout.propose import slugify
from ytscout.store import repo
from ytscout.youtube import DataApi, Ledger, QuotaExhausted, parse_duration
from ytscout.youtube.client import MAX_IDS_PER_CALL
from ytscout.youtube.quota import UNIT_COSTS

DEFAULT_MAX_SEARCHES = 10
MAX_SEARCHES_CAP = 15
SOURCE_VIDEOS = 3
SEARCH_RESULTS = 50
MIN_HITS = 3
MIN_JACCARD = 0.3
MIN_CLUSTER = 3
SHORTS_SHARE = 0.7
EXAMPLE_QUERIES = 3
TOPIC_WORDS = 2
# Approved competitors are competitors of the own channel, which is an animal channel.
COMPETITOR_CATEGORY = "animals_nature"
FALLBACK_CATEGORY = "entertainment_pop"


@dataclass(frozen=True)
class PlannedQuery:
    query: str
    source_channel: str
    source_video: str
    category: str | None


@dataclass(frozen=True)
class Hit:
    channel_id: str
    video_id: str
    title: str
    query: str


@dataclass(frozen=True)
class Proposal:
    format: str
    topic: str
    label: str
    topic_category: str
    category_fallback: bool
    queries: tuple[str, ...]
    seed_channel_ids: tuple[str, ...]
    keywords: tuple[str, ...]
    hit_count: int
    shorts_share: float | None

    def meta(self) -> dict[str, Any]:
        return {
            "category_fallback": self.category_fallback,
            "hit_count": self.hit_count,
            "keywords": list(self.keywords),
            "shorts_share": self.shorts_share,
        }


@dataclass
class SnowballResult:
    queries: list[PlannedQuery] = field(default_factory=list)
    searches: int = 0
    candidate_channels: int = 0
    kept_channels: int = 0
    clusters: int = 0
    proposals: list[Proposal] = field(default_factory=list)
    added: list[tuple[int, Proposal]] = field(default_factory=list)
    skipped: list[tuple[Proposal, str]] = field(default_factory=list)
    units: int = 0
    stopped: QuotaExhausted | None = None


# --- planning (no API) ----------------------------------------------------------------------


def plan_queries(conn: sqlite3.Connection, max_searches: int) -> list[PlannedQuery]:
    """The queries a run would search, most useful first, at most ``max_searches``."""
    categories = {int(r["id"]): r["topic_category"] for r in repo.list_niches(conn)}
    per_source: list[list[tuple[str, str, int]]] = []
    source_meta: list[tuple[str, str | None]] = []
    for channel_id, niche_id in repo.snowball_sources(conn):
        videos = repo.top_videos_by_views(conn, channel_id, SOURCE_VIDEOS)
        if videos:
            per_source.append(videos)
            category = COMPETITOR_CATEGORY if niche_id is None else categories.get(niche_id)
            source_meta.append((channel_id, category))
    # Sources with the biggest hit go first; then rank 1 of every source, rank 2, ...
    order = sorted(range(len(per_source)), key=lambda i: -per_source[i][0][2])
    used = {text.normalise_query(q) for q in repo.used_niche_queries(conn)}
    planned: list[PlannedQuery] = []
    seen: set[str] = set()
    for rank in range(SOURCE_VIDEOS):
        for i in order:
            if rank >= len(per_source[i]):
                continue
            video_id, title, _views = per_source[i][rank]
            query = text.snowball_query(title)
            if query is None:
                continue
            key = text.normalise_query(query)
            if key in seen or key in used:
                continue
            seen.add(key)
            channel_id, category = source_meta[i]
            planned.append(PlannedQuery(query, channel_id, video_id, category))
    return planned[: max(0, max_searches)]


def worst_case_units(searches: int) -> int:
    """The most a run of ``searches`` searches can cost (see the module docstring)."""
    max_channels = (searches * SEARCH_RESULTS) // MIN_HITS
    max_videos = searches * SEARCH_RESULTS
    return (
        searches * UNIT_COSTS[("search", "list")]
        + math.ceil(max_channels / MAX_IDS_PER_CALL) * UNIT_COSTS[("channels", "list")]
        + math.ceil(max_videos / MAX_IDS_PER_CALL) * UNIT_COSTS[("videos", "list")]
    )


def headroom(ledger: Ledger) -> int:
    """Units a run may still spend: the smaller of today's and ``--max-units``'s rest."""
    run_left = ledger.remaining_run()
    today_left = ledger.remaining_today()
    return today_left if run_left is None else min(today_left, run_left)


def searches_within(headroom: int, wanted: int) -> int:
    """The most searches (≤ ``wanted``) whose worst case fits in ``headroom`` units."""
    n = wanted
    while n > 0 and worst_case_units(n) > headroom:
        n -= 1
    return n


# --- clustering (pure) ----------------------------------------------------------------------


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def cluster(
    words: Mapping[str, set[str]], min_jaccard: float = MIN_JACCARD, min_size: int = MIN_CLUSTER
) -> list[list[str]]:
    """Single-linkage clusters of ``words``' keys: linked when Jaccard ≥ ``min_jaccard``.

    Clusters smaller than ``min_size`` are dropped. Members keep ``words``' order; clusters
    are ordered by size, then by their first member's position.
    """
    keys = list(words)
    parent = list(range(len(keys)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if jaccard(words[keys[i]], words[keys[j]]) >= min_jaccard:
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(keys)):
        groups.setdefault(find(i), []).append(i)
    kept = [g for g in groups.values() if len(g) >= min_size]
    kept.sort(key=lambda g: (-len(g), g[0]))
    return [[keys[i] for i in g] for g in kept]


def top_keywords(titles: Sequence[str], n: int = TOPIC_WORDS) -> list[str]:
    """The ``n`` content words in most titles (a title counts once); ties by first sighting."""
    counts: Counter[str] = Counter()
    first: dict[str, int] = {}
    for title in titles:
        words = text.tokens(title)
        for word in words:
            first.setdefault(word, len(first))
        counts.update(set(words))
    return sorted(counts, key=lambda w: (-counts[w], first[w]))[:n]


def propose(
    channel_ids: Sequence[str],
    hits: Mapping[str, Sequence[Hit]],
    durations: Mapping[str, int],
    query_order: Sequence[PlannedQuery],
    shorts_max_seconds: int,
) -> Proposal:
    """One cluster → one proposed niche (format, topic, label, category, example queries)."""
    cluster_hits = [h for c in channel_ids for h in hits[c]]
    videos = list(dict.fromkeys(h.video_id for h in cluster_hits))
    timed = [durations[v] for v in videos if v in durations]
    share = sum(1 for d in timed if d <= shorts_max_seconds) / len(timed) if timed else None
    fmt = "shorts" if share is not None and share >= SHORTS_SHARE else "longform"
    keywords = top_keywords([h.title for h in cluster_hits])
    by_query = Counter(h.query for h in cluster_hits)
    position = {p.query: i for i, p in enumerate(query_order)}
    examples = sorted(by_query, key=lambda q: (-by_query[q], position.get(q, len(position))))
    category_of = {p.query: p.category for p in query_order}
    categories = Counter(
        category_of[h.query] for h in cluster_hits if category_of.get(h.query) is not None
    )
    # most_common keeps first-counted order on ties.
    category = categories.most_common(1)[0][0] if categories else FALLBACK_CATEGORY
    return Proposal(
        format=fmt,
        topic=slugify(" ".join(keywords)),
        label=" ".join(keywords).capitalize(),
        topic_category=category,
        category_fallback=not categories,
        queries=tuple(examples[:EXAMPLE_QUERIES]),
        seed_channel_ids=tuple(channel_ids),
        keywords=tuple(keywords),
        hit_count=len(cluster_hits),
        shorts_share=None if share is None else round(share, 4),
    )


# --- the run --------------------------------------------------------------------------------


def _search(
    api: DataApi,
    queries: Sequence[PlannedQuery],
    published_after: datetime,
    language: str,
    excluded: set[str],
    result: SnowballResult,
) -> dict[str, list[Hit]]:
    hits: dict[str, list[Hit]] = {}
    for planned in queries:
        response = api.search(
            planned.query,
            order="viewCount",
            published_after=published_after,
            relevance_language=language,
            max_results=SEARCH_RESULTS,
        )
        result.searches += 1
        for item in response.get("items", []):
            snippet = item.get("snippet") or {}
            channel_id = snippet.get("channelId")
            video_id = (item.get("id") or {}).get("videoId")
            if not channel_id or not video_id or channel_id in excluded:
                continue
            title = snippet.get("title") or ""
            hits.setdefault(channel_id, []).append(Hit(channel_id, video_id, title, planned.query))
    return hits


def _existing_channels(api: DataApi, channel_ids: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for batch in batches(list(channel_ids)):
        found |= {item["id"] for item in api.channels(batch).get("items", [])}
    return found


def _durations(api: DataApi, video_ids: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for batch in batches(list(video_ids)):
        for item in api.videos(batch).get("items", []):
            raw = (item.get("contentDetails") or {}).get("duration")
            if raw:
                out[item["id"]] = parse_duration(raw)
    return out


def snowball(
    api: DataApi,
    conn: sqlite3.Connection,
    queries: Sequence[PlannedQuery],
    *,
    lookback_days: int,
    language: str,
    shorts_max_seconds: int,
    now: datetime,
) -> SnowballResult:
    """Search ``queries``, cluster the new channels, insert one proposed niche per cluster.

    On a quota stop ``result.stopped`` is set and nothing is written.
    """
    result = SnowballResult(queries=list(queries))
    excluded = repo.niche_channel_ids(conn) | {r["id"] for r in repo.tracked_channels(conn)}
    before = api.ledger.run_used
    try:
        hits = _search(
            api, queries, now - timedelta(days=lookback_days), language, excluded, result
        )
        result.candidate_channels = len(hits)
        frequent = [c for c, h in hits.items() if len(h) >= MIN_HITS]
        existing = _existing_channels(api, frequent) if frequent else set()
        kept = [c for c in frequent if c in existing]
        result.kept_channels = len(kept)
        words = {c: {w for h in hits[c] for w in text.tokens(h.title)} for c in kept}
        clusters = cluster(words)
        result.clusters = len(clusters)
        video_ids = list(dict.fromkeys(h.video_id for g in clusters for c in g for h in hits[c]))
        durations = _durations(api, video_ids) if video_ids else {}
    except QuotaExhausted as exc:
        result.stopped = exc
        return result
    finally:
        result.units = api.ledger.run_used - before

    result.proposals = [
        propose(group, hits, durations, queries, shorts_max_seconds) for group in clusters
    ]
    with conn:
        for proposal in result.proposals:
            if not proposal.keywords:
                result.skipped.append((proposal, "no content words"))
                continue
            if repo.niche_exists(conn, proposal.format, proposal.topic):
                result.skipped.append((proposal, "niche already exists"))
                continue
            niche_id = repo.insert_niche(
                conn,
                fmt=proposal.format,
                topic=proposal.topic,
                topic_category=proposal.topic_category,
                label=proposal.label,
                source="snowball",
                queries=proposal.queries,
                required_steps=[],
                meta=proposal.meta(),
                created_at=now,
                seed_channel_ids=proposal.seed_channel_ids,
            )
            result.added.append((niche_id, proposal))
    return result


def format_proposal(niche_id: int | None, p: Proposal) -> str:
    head = f"niche {niche_id}" if niche_id is not None else "proposal"
    flag = " (category fallback)" if p.category_fallback else ""
    return (
        f"{head} [{p.format}] {p.topic} ({p.topic_category}{flag}): "
        f"{len(p.seed_channel_ids)} channels, {p.hit_count} hits; "
        f"queries {', '.join(repr(q) for q in p.queries)}"
    )
