"""The niche relevance gate (049): is a channel a search returned really in the niche?

``scout validate`` samples whatever channels ``search.list`` returns, and in 027 that meant
BBC News leading "aviation incidents" and Kurzgesagt leading "dangerous animals". Before
``score --niches`` builds a sample, every linked channel is screened on the titles and
categories of its newest stored videos; a channel that fails keeps its ``niche_channels``
row with ``excluded_reason`` set, and ``build_sample`` leaves it out.

The checks, first failure wins:

1. **News outlet**: ``news_share_min`` of the videos are in a news category.
2. **Script**: the titles are under ``discovery.latin_share_min`` Latin letters.
3. **Language** (English only): ``foreign_title_share_min`` of the titles carry a
   non-English function word and those titles outnumber the ones with an English one.
   Stored videos have no language tag, so this is the stand-in for discovery's tag check.
4. **Topic**: fewer than ``topical_title_share_min`` of the titles share a word with the
   niche's queries or label (``text.tokens``, the same words discovery's keyword overlap
   uses). No shared word at all always fails.

A channel with no stored videos is kept: no evidence either way.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ytscout.collect.discover import latin_share
from ytscout.scoring import RelevanceConfig
from ytscout.store import repo
from ytscout.text import tokens

# Short function words that mark a title as English or as another Latin-script language
# (French, Spanish, Portuguese, German, Italian, Dutch, Polish, Turkish, Vietnamese).
# Words both sides use ("a", "in", "die", "do", "den") are in neither list.
ENGLISH_WORDS = frozenset(
    """
    the and of to is with for how why what this you your from are was that it my who when
    will can were has have
    """.split()
)
FOREIGN_WORDS = frozenset(
    """
    le la les des du une est et pour avec dans sur qui el los las por con del una que para
    como os da dos não um uma der das und ist nicht mit ein eine il di che per sono een het
    van op zijn nie jak się na bir bu ile và của là
    """.split()
)
_WORD = re.compile(r"[^\W\d_]+")


def niche_terms(niche: Mapping[str, Any]) -> set[str]:
    """The words of the niche's queries, label and topic, as ``text.tokens`` sees them."""
    raw = json.loads(niche["queries_json"] or "[]")
    texts = [q for q in raw if isinstance(q, str)]
    texts += [niche["label"] or "", niche["topic"] or ""]
    return {word for text in texts for word in tokens(text)}


def _share(n: int, total: int) -> float:
    return n / total if total else 0.0


def exclusion_reason(
    titles: Sequence[str],
    category_ids: Sequence[str | None],
    terms: set[str],
    cfg: RelevanceConfig,
) -> str | None:
    """Why the channel with these newest-video ``titles`` and ``category_ids`` is not in the
    niche whose words are ``terms``; ``None`` when it is (or there are no videos)."""
    if not titles:
        return None
    news = sum(1 for c in category_ids if c is not None and str(c) in cfg.news_category_ids)
    if category_ids and _share(news, len(category_ids)) >= cfg.news_share_min:
        return f"news outlet: {news} of {len(category_ids)} videos in a news category"
    latin = latin_share(titles)
    if latin < cfg.latin_share_min:
        return f"not {cfg.language}: titles {latin:.0%} Latin < {cfg.latin_share_min:.0%}"
    if cfg.language == "en":
        words = [set(_WORD.findall(t.lower())) for t in titles]
        foreign = sum(1 for w in words if w & FOREIGN_WORDS)
        english = sum(1 for w in words if w & ENGLISH_WORDS)
        if _share(foreign, len(titles)) >= cfg.foreign_title_share_min and foreign > english:
            return (
                f"not en: {foreign} of {len(titles)} titles use non-English words,"
                f" {english} English"
            )
    topical = sum(1 for t in titles if terms & set(tokens(t)))
    share = _share(topical, len(titles))
    if topical == 0 or share < cfg.topical_title_share_min:
        return (
            f"off-topic: {topical} of {len(titles)} titles share a niche word"
            f" ({share:.0%} < {cfg.topical_title_share_min:.0%})"
        )
    return None


def refresh(conn: sqlite3.Connection, niche_id: int, cfg: RelevanceConfig) -> tuple[int, int]:
    """Screen every channel linked to the niche and store the verdicts. Does not commit.
    Returns ``(counted, excluded)``."""
    niche = repo.get_niche(conn, niche_id)
    if niche is None:
        raise LookupError(f"no niche {niche_id!r}")
    terms = niche_terms(niche)
    counted = excluded = 0
    videos = repo.niche_channel_recent_videos(conn, niche_id, cfg.titles_per_channel)
    for channel_id, rows in videos.items():
        reason = exclusion_reason(
            [r["title"] or "" for r in rows], [r["category_id"] for r in rows], terms, cfg
        )
        repo.set_niche_channel_exclusion(conn, niche_id, channel_id, reason)
        if reason is None:
            counted += 1
        else:
            excluded += 1
    return counted, excluded


def refresh_all(
    conn: sqlite3.Connection, cfg: RelevanceConfig, niche_ids: Iterable[int] | None = None
) -> dict[int, tuple[int, int]]:
    """``refresh`` every sampled niche (only ``niche_ids`` when given), shelved ones too,
    so the dashboard shows what each sample excludes. Does not commit."""
    wanted = None if niche_ids is None else set(niche_ids)
    return {
        niche_id: refresh(conn, niche_id, cfg)
        for niche_id in repo.sampled_niche_ids(conn)
        if wanted is None or niche_id in wanted
    }
