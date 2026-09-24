"""``scout propose``: niche candidates from ``config/seed_niches.yaml`` or a Claude brainstorm.

A niche is a ``format × topic`` pair (DESIGN.md §5.2). This module owns the three config
files that make a candidate scoreable later, and the one ingest path both feeds share:

* ``config/rpm_tiers.yaml``: the fixed ``TOPIC_CATEGORIES`` × ``{shorts, longform}`` table of
  USD RPM ranges, every row with a ``source`` and ``last_reviewed``. The category ids are
  the join key between niches and money (§6.3); ``load_rpm_tiers`` refuses a file that does
  not hold exactly those ids.
* ``config/seed_niches.yaml``: hand-written candidates. ``--from-seeds`` loads them.
* ``prompts/niche_brainstorm.md`` + ``schemas/niche_brainstorm.json``: the LLM feed. The
  packet carries the production steps, the pipeline coverage, the constraints and the
  labels of every niche already in the DB, so Claude explores rather than repeats.

``ingest`` de-duplicates on ``(format, topic)`` against the DB and within the batch, and
skips a candidate whose ``topic_category`` is not in the RPM table or whose
``suspected_manual_steps`` name a step id ``production_steps.yaml`` does not have; the
counts come back in ``ProposeResult`` for the run summary.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ytscout import claude_runner, packets
from ytscout.audit import COVERAGE_RELPATH, STEPS_RELPATH, load_coverage, load_steps
from ytscout.store import repo, utc_now

RPM_TIERS_RELPATH = Path("config") / "rpm_tiers.yaml"
SEEDS_RELPATH = Path("config") / "seed_niches.yaml"
PROMPT_RELPATH = Path("prompts") / "niche_brainstorm.md"
SCHEMA_RELPATH = Path("schemas") / "niche_brainstorm.json"
PACKET_KIND = "niche_brainstorm"
DEFAULT_COUNT = 30
QUERIES_PER_NICHE = 3
FORMATS: tuple[str, ...] = repo.NICHE_FORMATS
SOURCE_SEED = "seed"
SOURCE_LLM = "llm"

# The fixed list (issue 021). rpm_tiers.yaml and schemas/niche_brainstorm.json must carry
# exactly these ids; tests/test_scout_propose.py checks all three agree.
TOPIC_CATEGORIES: tuple[str, ...] = (
    "finance_business",
    "tech_software",
    "education_explainer",
    "health_fitness",
    "true_crime_mystery",
    "history_science",
    "animals_nature",
    "entertainment_pop",
    "gaming",
    "lifestyle_travel",
    "diy_home",
    "cars_vehicles",
    "food_cooking",
    "kids_family",
)

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NOT_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ScoutConfigError(Exception):
    """A scout YAML file is missing, malformed or inconsistent with the other config."""


# --- rpm_tiers.yaml ------------------------------------------------------------------------


@dataclass(frozen=True)
class RpmRow:
    low: float
    mid: float
    high: float
    source: str
    last_reviewed: str
    notes: str = ""


@dataclass(frozen=True)
class RpmCategory:
    id: str
    label: str
    description: str
    rows: dict[str, RpmRow]  # format -> row

    def row(self, fmt: str) -> RpmRow:
        return self.rows[fmt]


@dataclass(frozen=True)
class RpmTable:
    categories: dict[str, RpmCategory]
    path: Path

    def row(self, category: str, fmt: str) -> RpmRow:
        return self.categories[category].rows[fmt]

    @property
    def category_ids(self) -> tuple[str, ...]:
        return tuple(self.categories)


def _read_yaml(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ScoutConfigError(f"{path}: not found")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScoutConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ScoutConfigError(f"{path}: top level must be a mapping")
    return doc


def _number(where: str, key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ScoutConfigError(f"{where}: {key} must be a number, got {value!r}")
    if value < 0:
        raise ScoutConfigError(f"{where}: {key} must not be negative, got {value!r}")
    return float(value)


def _text(where: str, key: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoutConfigError(f"{where}: {key} is required and must be non-empty text")
    return value.strip()


def _rpm_row(where: str, raw: Any) -> RpmRow:
    if not isinstance(raw, dict):
        raise ScoutConfigError(f"{where}: must be a mapping")
    usd = raw.get("usd_rpm")
    if not isinstance(usd, dict):
        raise ScoutConfigError(f"{where}: usd_rpm must be a mapping of low/mid/high")
    low = _number(where, "usd_rpm.low", usd.get("low"))
    mid = _number(where, "usd_rpm.mid", usd.get("mid"))
    high = _number(where, "usd_rpm.high", usd.get("high"))
    if not low <= mid <= high:
        raise ScoutConfigError(f"{where}: usd_rpm must satisfy low <= mid <= high")
    last_reviewed = str(raw.get("last_reviewed") or "")
    if not _MONTH.match(last_reviewed):
        raise ScoutConfigError(f"{where}: last_reviewed must be YYYY-MM, got {last_reviewed!r}")
    return RpmRow(
        low=low,
        mid=mid,
        high=high,
        source=_text(where, "source", raw.get("source")),
        last_reviewed=last_reviewed,
        notes=str(raw.get("notes") or "").strip(),
    )


def load_rpm_tiers(path: Path) -> RpmTable:
    """Load and validate ``rpm_tiers.yaml``: exactly ``TOPIC_CATEGORIES``, both formats each."""
    path = Path(path)
    doc = _read_yaml(path)
    raw_categories = doc.get("categories")
    if not isinstance(raw_categories, dict) or not raw_categories:
        raise ScoutConfigError(f"{path}: 'categories' must be a non-empty mapping")
    missing = [c for c in TOPIC_CATEGORIES if c not in raw_categories]
    extra = [c for c in raw_categories if c not in TOPIC_CATEGORIES]
    if missing or extra:
        raise ScoutConfigError(
            f"{path}: categories must be exactly the fixed list; missing {missing}, unknown {extra}"
        )
    categories: dict[str, RpmCategory] = {}
    for cat_id in TOPIC_CATEGORIES:
        raw = raw_categories[cat_id]
        where = f"{path}: {cat_id}"
        if not isinstance(raw, dict):
            raise ScoutConfigError(f"{where}: must be a mapping")
        rows = {}
        for fmt in FORMATS:
            if fmt not in raw:
                raise ScoutConfigError(f"{where}: missing format {fmt!r}")
            rows[fmt] = _rpm_row(f"{where}.{fmt}", raw[fmt])
        categories[cat_id] = RpmCategory(
            id=cat_id,
            label=_text(where, "label", raw.get("label")),
            description=str(raw.get("description") or "").strip(),
            rows=rows,
        )
    return RpmTable(categories=categories, path=path)


# --- candidates ----------------------------------------------------------------------------


def slugify(text: str) -> str:
    """``"Top 5 Dangerous Animals"`` → ``"top-5-dangerous-animals"``."""
    return _NOT_SLUG_CHARS.sub("-", text.strip().lower()).strip("-")


@dataclass(frozen=True)
class NicheCandidate:
    format: str
    topic: str
    label: str
    topic_category: str
    example_search_queries: tuple[str, ...]
    why_ai_able: str | None = None
    suspected_manual_steps: tuple[str, ...] = ()
    evergreen: bool | None = None
    faceless_ok: bool | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.format, self.topic

    def meta(self) -> dict:
        return {
            "why_ai_able": self.why_ai_able,
            "evergreen": self.evergreen,
            "faceless_ok": self.faceless_ok,
        }


def _candidate(where: str, raw: Any, *, strict: bool) -> NicheCandidate:
    """One candidate from a YAML seed (``strict``: category checked too) or Claude output."""
    if not isinstance(raw, dict):
        raise ScoutConfigError(f"{where}: must be a mapping")
    fmt = raw.get("format")
    if fmt not in FORMATS:
        raise ScoutConfigError(f"{where}: format must be one of {FORMATS}, got {fmt!r}")
    topic = slugify(_text(where, "topic", raw.get("topic")))
    if not _SLUG.match(topic):
        raise ScoutConfigError(f"{where}: topic {raw.get('topic')!r} is not a slug")
    queries = raw.get("example_search_queries")
    if (
        not isinstance(queries, list)
        or len(queries) != QUERIES_PER_NICHE
        or not all(isinstance(q, str) and q.strip() for q in queries)
    ):
        raise ScoutConfigError(
            f"{where}: example_search_queries must be exactly {QUERIES_PER_NICHE} non-empty strings"
        )
    steps = raw.get("suspected_manual_steps")
    if steps is None:
        steps = []
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ScoutConfigError(f"{where}: suspected_manual_steps must be a list of step ids")
    category = _text(where, "topic_category", raw.get("topic_category"))
    if strict and category not in TOPIC_CATEGORIES:
        raise ScoutConfigError(f"{where}: unknown topic_category {category!r}")
    evergreen = raw.get("evergreen")
    faceless_ok = raw.get("faceless_ok")
    for key, value in (("evergreen", evergreen), ("faceless_ok", faceless_ok)):
        if value is not None and not isinstance(value, bool):
            raise ScoutConfigError(f"{where}: {key} must be true or false")
    why = raw.get("why_ai_able")
    return NicheCandidate(
        format=str(fmt),
        topic=topic,
        label=_text(where, "label", raw.get("label")),
        topic_category=category,
        example_search_queries=tuple(q.strip() for q in queries),
        why_ai_able=None if why is None else str(why).strip(),
        suspected_manual_steps=tuple(dict.fromkeys(steps)),
        evergreen=evergreen,
        faceless_ok=faceless_ok,
    )


def load_seeds(path: Path) -> list[NicheCandidate]:
    """Load ``seed_niches.yaml``: a ``niches`` list, every entry fully valid."""
    path = Path(path)
    doc = _read_yaml(path)
    raw = doc.get("niches")
    if not isinstance(raw, list) or not raw:
        raise ScoutConfigError(f"{path}: 'niches' must be a non-empty list")
    seeds = [_candidate(f"{path}: niches[{i}]", item, strict=True) for i, item in enumerate(raw)]
    seen: set[tuple[str, str]] = set()
    for seed in seeds:
        if seed.key in seen:
            raise ScoutConfigError(f"{path}: duplicate seed {seed.format} x {seed.topic}")
        seen.add(seed.key)
    return seeds


def candidates_from_output(output: dict) -> list[NicheCandidate]:
    """The brainstorm's validated ``structured_output`` as candidates (categories unchecked)."""
    return [
        _candidate(f"niches[{i}]", item, strict=False)
        for i, item in enumerate(output.get("niches", []))
    ]


# --- ingest --------------------------------------------------------------------------------


@dataclass(frozen=True)
class AddedNiche:
    id: int
    format: str
    topic: str
    label: str
    topic_category: str


@dataclass
class ProposeResult:
    source: str
    candidates: int = 0
    added: list[AddedNiche] = field(default_factory=list)
    skipped_duplicate: int = 0
    skipped_category: int = 0
    skipped_steps: int = 0
    unknown_categories: list[str] = field(default_factory=list)
    unknown_steps: list[str] = field(default_factory=list)
    # Brainstorm only.
    prompt_hash: str = ""
    schema_hash: str = ""
    packet_path: Path | None = None
    duration_s: float = 0.0
    total_cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    failure: str | None = None

    @property
    def skipped(self) -> int:
        return self.skipped_duplicate + self.skipped_category + self.skipped_steps


def step_ids(repo_root: Path) -> set[str]:
    return {step.id for step in load_steps(Path(repo_root) / STEPS_RELPATH)}


def ingest(
    conn: sqlite3.Connection,
    candidates: list[NicheCandidate],
    *,
    source: str,
    categories: set[str] | frozenset[str],
    known_steps: set[str] | frozenset[str],
    meta_extra: dict | None = None,
) -> ProposeResult:
    """Insert the candidates that pass, in one transaction; count the ones that do not.

    A candidate is skipped when its category is not in ``categories``, when any of its
    suspected steps is not in ``known_steps``, or when its ``(format, topic)`` is already
    in the DB or earlier in this batch. Checks run in that order, so a row is counted once.
    """
    result = ProposeResult(source=source, candidates=len(candidates))
    seen: set[tuple[str, str]] = set()
    with conn:
        for cand in candidates:
            if cand.topic_category not in categories:
                result.skipped_category += 1
                result.unknown_categories.append(cand.topic_category)
                continue
            bad_steps = [s for s in cand.suspected_manual_steps if s not in known_steps]
            if bad_steps:
                result.skipped_steps += 1
                result.unknown_steps.extend(bad_steps)
                continue
            if cand.key in seen or repo.niche_exists(conn, cand.format, cand.topic):
                result.skipped_duplicate += 1
                continue
            seen.add(cand.key)
            row_id = repo.insert_niche(
                conn,
                fmt=cand.format,
                topic=cand.topic,
                topic_category=cand.topic_category,
                label=cand.label,
                source=source,
                queries=cand.example_search_queries,
                required_steps=cand.suspected_manual_steps,
                meta={**cand.meta(), **(meta_extra or {})},
            )
            result.added.append(
                AddedNiche(row_id, cand.format, cand.topic, cand.label, cand.topic_category)
            )
    return result


# --- the two feeds ---------------------------------------------------------------------------


def prompt_paths(repo_root: Path) -> tuple[Path, Path]:
    return Path(repo_root) / PROMPT_RELPATH, Path(repo_root) / SCHEMA_RELPATH


def propose_from_seeds(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    seeds_path: Path | None = None,
    rpm_path: Path | None = None,
) -> ProposeResult:
    """Load the seeds YAML into ``niches`` as ``source='seed'``; idempotent."""
    repo_root = Path(repo_root)
    rpm = load_rpm_tiers(rpm_path or repo_root / RPM_TIERS_RELPATH)
    seeds = load_seeds(seeds_path or repo_root / SEEDS_RELPATH)
    return ingest(
        conn,
        seeds,
        source=SOURCE_SEED,
        categories=set(rpm.category_ids),
        known_steps=step_ids(repo_root),
    )


def brainstorm_packet(
    conn: sqlite3.Connection, *, repo_root: Path, count: int, rpm: RpmTable | None = None
) -> dict:
    """Everything the brainstorm prompt may use.

    Steps with their pipeline coverage, the categories, the constraints, every niche
    already in the DB, and the reference niche (the first seed) when the seeds file loads.
    """
    repo_root = Path(repo_root)
    rpm = rpm or load_rpm_tiers(repo_root / RPM_TIERS_RELPATH)
    steps = load_steps(repo_root / STEPS_RELPATH)
    coverage = load_coverage(repo_root / COVERAGE_RELPATH)
    try:
        reference: dict | None = _reference(load_seeds(repo_root / SEEDS_RELPATH)[0])
    except ScoutConfigError:
        reference = None
    existing = [
        {"format": row["format"], "topic": row["topic"], "label": row["label"]}
        for row in repo.list_niches(conn)
    ]
    production_steps = []
    for step in steps:
        cov = coverage.steps.get(step.id)
        production_steps.append(
            {
                "id": step.id,
                "label": step.label,
                "default_hours": step.default_hours,
                "specific_footage_hours": step.specific_footage_hours,
                "disqualifying_for_faceless": step.disqualifying_for_faceless,
                "pipeline_coverage": cov.coverage if cov else "manual",
                "manual_hours_override": cov.manual_hours_override if cov else None,
                "notes": step.notes,
            }
        )
    return {
        "meta": {
            "built_at": utc_now().isoformat(timespec="seconds").replace("+00:00", "Z"),
            "count": count,
            "pipeline_commit": coverage.pipeline_commit,
        },
        "count": count,
        "formats": {
            "shorts": "vertical video up to 180 seconds; no custom thumbnail; feed-driven",
            "longform": "horizontal video, typically 8-15 minutes; needs a thumbnail; "
            "search- and suggestion-driven",
        },
        "formats_pipeline_supports": list(coverage.formats_supported),
        "topic_categories": [
            {"id": c.id, "label": c.label, "description": c.description}
            for c in rpm.categories.values()
        ],
        "production_steps": production_steps,
        "constraints": {
            "faceless": "no presenter, no face-cam; steps marked disqualifying_for_faceless "
            "must not be required",
            "language": "English, worldwide audience",
            "evergreen": "preferred: topics that keep getting views for years, not news",
            "formats": "propose both shorts and longform niches, roughly half each",
            "production": "the video is research + script + TTS voiceover + stock/AI "
            "visuals + automated assembly; niches that need original footage, screen "
            "recording or a presenter do not fit",
        },
        "existing_niches": existing,
        "reference_niche": reference,
    }


def _reference(seed: NicheCandidate) -> dict:
    return {
        "format": seed.format,
        "topic": seed.topic,
        "label": seed.label,
        "topic_category": seed.topic_category,
        "note": "the channel's current niche; propose things like it in effort, not in topic",
    }


def propose_from_brainstorm(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    packets_dir: Path,
    count: int = DEFAULT_COUNT,
    model: str | None = None,
    prompt_path: Path | None = None,
    schema_path: Path | None = None,
) -> ProposeResult:
    """One ``claude -p`` brainstorm → candidates → ``ingest`` as ``source='llm'``.

    Caller has checked ``claude`` is usable. A ``ClaudeUnavailable`` comes back in
    ``failure`` with nothing written; nothing is raised.
    """
    repo_root = Path(repo_root)
    default_prompt, default_schema = prompt_paths(repo_root)
    prompt_path = Path(prompt_path or default_prompt)
    schema_path = Path(schema_path or default_schema)
    rpm = load_rpm_tiers(repo_root / RPM_TIERS_RELPATH)
    known_steps = step_ids(repo_root)
    packet = brainstorm_packet(conn, repo_root=repo_root, count=count, rpm=rpm)
    packet_path = packets.write_packet(PACKET_KIND, packet, packets_dir)
    prompt_hash = claude_runner.file_hash(prompt_path)
    schema_hash = claude_runner.file_hash(schema_path)
    failed = ProposeResult(
        source=SOURCE_LLM,
        prompt_hash=prompt_hash,
        schema_hash=schema_hash,
        packet_path=packet_path,
    )
    try:
        run = claude_runner.run(prompt_path, packet_path, schema_path, model=model, cwd=repo_root)
    except claude_runner.ClaudeUnavailable as exc:
        failed.failure = str(exc)
        return failed
    try:
        candidates = candidates_from_output(run.structured_output)
    except ScoutConfigError as exc:
        failed.failure = f"brainstorm output unusable: {exc}"
        return failed
    result = ingest(
        conn,
        candidates,
        source=SOURCE_LLM,
        categories=set(rpm.category_ids),
        known_steps=known_steps,
        meta_extra={
            "prompt_hash": run.prompt_hash,
            "schema_hash": run.schema_hash,
            "packet_path": str(packet_path),
        },
    )
    result.prompt_hash = run.prompt_hash
    result.schema_hash = run.schema_hash
    result.packet_path = packet_path
    result.duration_s = run.duration_s
    result.total_cost_usd = run.total_cost_usd or 0.0
    result.usage = run.usage
    return result


# --- output ----------------------------------------------------------------------------------


def format_added(result: ProposeResult) -> str:
    """A plain table of the rows added this run, or one line saying none were."""
    if not result.added:
        return "added: none"
    rows = [("id", "format", "topic", "category", "label")]
    rows += [(str(a.id), a.format, a.topic, a.topic_category, a.label) for a in result.added]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for n, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def format_summary(result: ProposeResult) -> str:
    return (
        f"scout propose ({result.source}): {result.candidates} candidate(s), "
        f"{len(result.added)} added, {result.skipped_duplicate} duplicate(s) skipped, "
        f"{result.skipped_category} with an unknown category, "
        f"{result.skipped_steps} with an unknown step id"
    )
