"""``scout tag``: Claude says which production steps each validated niche needs.

Up to ``BATCH_SIZE`` niches per ``claude -p`` call. The packet carries each niche's label,
format, category, search queries, ``why_ai_able`` and the titles of the most-viewed
videos from its validated channels, plus ``production_steps.yaml``. The answer replaces
``required_steps_json`` (until then it holds the brainstorm's guess) and sets
``needs_specific_footage``, ``reused_content_risk`` / ``reused_content_reason`` (055: how
likely YouTube's reused-content policy is to refuse the pipeline's videos in this niche)
and ``tag_prompt_hash``. A niche is re-tagged when the prompt changes; ``score --niches``
skips a niche that has never been tagged.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ytscout import claude_runner, packets
from ytscout.audit import STEPS_RELPATH, Step, load_steps
from ytscout.store import repo, to_utc_iso, utc_now

PROMPT_RELPATH = Path("prompts") / "niche_step_tagging.md"
SCHEMA_RELPATH = Path("schemas") / "niche_step_tagging.json"
PACKET_KIND = "niche_step_tagging"
BATCH_SIZE = 10
SAMPLE_TITLES = 5
DEFAULT_LIMIT = 50


def prompt_paths(repo_root: Path) -> tuple[Path, Path]:
    return Path(repo_root) / PROMPT_RELPATH, Path(repo_root) / SCHEMA_RELPATH


def batches(rows: list[sqlite3.Row], size: int = BATCH_SIZE) -> list[list[sqlite3.Row]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def sample_titles(
    conn: sqlite3.Connection, niche: sqlite3.Row, shorts_max_seconds: int, n: int = SAMPLE_TITLES
) -> list[str]:
    """The ``n`` most-viewed titles among the niche's channels, in the niche's format when
    the duration is known."""
    want_short = niche["format"] == "shorts"
    rows = [
        r
        for r in repo.niche_sample_videos(conn, niche["id"])
        if r["title"]
        and (r["duration_s"] is None or (r["duration_s"] <= shorts_max_seconds) == want_short)
    ]
    rows.sort(key=lambda r: (-(r["views"] or 0), r["id"]))
    titles: list[str] = []
    for r in rows:
        if r["title"] not in titles:
            titles.append(r["title"])
        if len(titles) == n:
            break
    return titles


def _json_list(text: str | None) -> list:
    try:
        value = json.loads(text) if text else []
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _meta(niche: sqlite3.Row) -> dict:
    try:
        value = json.loads(niche["meta_json"]) if niche["meta_json"] else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def tag_packet(
    conn: sqlite3.Connection,
    niches: list[sqlite3.Row],
    steps: list[Step],
    *,
    shorts_max_seconds: int,
) -> dict:
    return {
        "meta": {"built_at": to_utc_iso(utc_now()), "niches": len(niches)},
        "production_steps": [
            {
                "id": s.id,
                "label": s.label,
                "default_hours": s.default_hours,
                "specific_footage_hours": s.specific_footage_hours,
                "disqualifying_for_faceless": s.disqualifying_for_faceless,
                "notes": s.notes,
            }
            for s in steps
        ],
        "niches": [
            {
                "niche_id": n["id"],
                "label": n["label"] or n["topic"],
                "format": n["format"],
                "topic_category": n["topic_category"],
                "queries": _json_list(n["queries_json"]),
                "why_ai_able": _meta(n).get("why_ai_able") or "",
                "sample_titles": sample_titles(conn, n, shorts_max_seconds),
            }
            for n in niches
        ],
    }


@dataclass
class TagResult:
    candidates: int = 0
    calls: int = 0
    tagged: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)  # asked about, not in the answer
    prompt_hash: str = ""
    packet_paths: list[Path] = field(default_factory=list)
    duration_s: float = 0.0
    failure: str | None = None


def tag_niches(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    packets_dir: Path,
    shorts_max_seconds: int,
    limit: int = DEFAULT_LIMIT,
    model: str | None = None,
) -> TagResult:
    """Tag up to ``limit`` niches, ``BATCH_SIZE`` per call, each batch committed on its own.

    Stops at the first ``ClaudeUnavailable`` (returned in ``failure``); batches already
    answered stay written. Nothing is raised for a Claude failure.
    """
    repo_root = Path(repo_root)
    prompt_path, schema_path = prompt_paths(repo_root)
    steps = load_steps(repo_root / STEPS_RELPATH)
    known = {s.id for s in steps}
    prompt_hash = claude_runner.file_hash(prompt_path)
    result = TagResult(prompt_hash=prompt_hash)
    targets = repo.niches_to_tag(conn, prompt_hash, limit)
    result.candidates = len(targets)
    for batch in batches(targets):
        packet = tag_packet(conn, batch, steps, shorts_max_seconds=shorts_max_seconds)
        packet_path = packets.write_packet(PACKET_KIND, packet, packets_dir)
        result.packet_paths.append(packet_path)
        try:
            run = claude_runner.run(
                prompt_path, packet_path, schema_path, model=model, cwd=repo_root
            )
        except claude_runner.ClaudeUnavailable as exc:
            result.failure = str(exc)
            return result
        result.calls += 1
        result.duration_s += run.duration_s
        answers = {a["niche_id"]: a for a in run.structured_output["niches"]}
        tagged_at = utc_now()
        with conn:
            for niche in batch:
                answer = answers.get(niche["id"])
                if answer is None:
                    result.missing.append(niche["id"])
                    continue
                required = [s for s in dict.fromkeys(answer["required_steps"]) if s in known]
                risk = answer.get("reused_content_risk")
                repo.set_niche_tags(
                    conn,
                    niche["id"],
                    required_steps=required,
                    needs_specific_footage=answer["needs_specific_footage"],
                    notes=answer["notes"],
                    prompt_hash=run.prompt_hash,
                    schema_hash=run.schema_hash,
                    tagged_at=tagged_at,
                    reused_content_risk=risk if risk in repo.REUSED_CONTENT_RISKS else None,
                    reused_content_reason=answer.get("reused_content_reason") or None,
                )
                result.tagged.append(niche["id"])
    return result
