"""Title text → seed search queries for competitor discovery (DESIGN.md §4.3).

Pure functions, no I/O. ``seed_queries`` turns the own channel's recent titles and its
channel keywords into the ``search.list`` queries ``discover`` runs.
"""

from __future__ import annotations

import html
import re
import shlex
from collections import Counter
from collections.abc import Iterable, Sequence

# Tokens that say "this is a countdown Short" rather than what the video is about.
FORMAT_TOKENS = frozenset({"top", "5", "five", "countdown", "shorts", "#shorts"})

# 100 common English stop words.
STOP_WORDS = frozenset(
    """
    a about above after again all an and any are as at be because been before between both
    but by can could did do does down during each few for from had has have he her here him
    his how i if in into is it its just me more most my no not of off on only or other our
    out over own she should so some such than that the their them then there these they this
    those through to too under up very was we were what when where which while who why will
    with you your
    """.split()
)

QUERY_PREFIX = "top 5 "
TOP_BIGRAMS = 8
TOP_UNIGRAMS = 4
MIN_COUNT = 2

_DIGITS = re.compile(r"\d+")
_WORD = re.compile(r"#?[a-z]+(?:'[a-z]+)*")


def tokens(text: str) -> list[str]:
    """Lower-case words of ``text`` with numerals, format tokens and stop words removed.

    ``"Top 5 DEADLIEST Snakes #shorts"`` → ``["deadliest", "snakes"]``. HTML entities
    (``search.list`` titles carry ``&#39;``) are decoded first.
    """
    text = _DIGITS.sub(" ", html.unescape(text).lower())
    out = []
    for word in _WORD.findall(text):
        if word in FORMAT_TOKENS or word.lstrip("#") in FORMAT_TOKENS:
            continue
        word = word.lstrip("#")
        if word and word not in STOP_WORDS:
            out.append(word)
    return out


def _ranked(counter: Counter[str], first_seen: dict[str, int], limit: int) -> list[str]:
    """The ``limit`` most frequent keys with count ≥ ``MIN_COUNT``; ties by first sighting."""
    eligible = [k for k, n in counter.items() if n >= MIN_COUNT]
    eligible.sort(key=lambda k: (-counter[k], first_seen[k]))
    return eligible[:limit]


def title_ngrams(titles: Iterable[str]) -> tuple[list[str], list[str]]:
    """``(bigrams, unigrams)``: the top 8 2-grams and top 4 1-grams seen in ≥ 2 titles.

    A title counts once per n-gram however often it repeats it, so one keyword-stuffed
    title cannot make a query on its own. 2-grams join adjacent tokens *after* stripping.
    """
    unigrams: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for title in titles:
        words = tokens(title)
        pairs = [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
        for gram in (*words, *pairs):
            first_seen.setdefault(gram, len(first_seen))
        unigrams.update(set(words))
        bigrams.update(set(pairs))
    return _ranked(bigrams, first_seen, TOP_BIGRAMS), _ranked(unigrams, first_seen, TOP_UNIGRAMS)


def parse_keywords(raw: str | None) -> list[str]:
    """``brandingSettings.channel.keywords`` → tags. Multi-word tags come double-quoted."""
    if not raw or not raw.strip():
        return []
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    return [p.strip() for p in parts if p.strip()]


def seed_queries(titles: Sequence[str], keywords: Sequence[str], max_queries: int) -> list[str]:
    """Search queries, most useful first: ``top 5 <2-gram>`` ×8, ``top 5 <1-gram>`` ×4, tags.

    Tags are lower-cased and used as they are; a tag with nothing left after
    ``tokens()`` (``"shorts"``, ``"top 5"``) is skipped. De-duplicated, then capped.
    """
    bigrams, unigrams = title_ngrams(titles)
    queries = [QUERY_PREFIX + gram for gram in [*bigrams, *unigrams]]
    for tag in keywords:
        tag = " ".join(tag.lower().split())
        if tokens(tag):
            queries.append(tag)
    unique = list(dict.fromkeys(queries))
    return unique[: max(0, max_queries)]


def query_terms(queries: Iterable[str]) -> set[str]:
    """The distinct content words across ``queries`` (the ``top 5`` prefix drops out)."""
    return {word for query in queries for word in tokens(query)}
