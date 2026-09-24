"""``scout propose`` (021): the RPM table, the seeds, the brainstorm and the ingest rules."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from ytscout.audit import STEPS_RELPATH, load_steps
from ytscout.claude_runner import SchemaError, file_hash, validate
from ytscout.cli import EXIT_CLAUDE_UNAVAILABLE, EXIT_ERROR, EXIT_OK, main
from ytscout.scout.propose import (
    DEFAULT_COUNT,
    FORMATS,
    QUERIES_PER_NICHE,
    RPM_TIERS_RELPATH,
    SEEDS_RELPATH,
    TOPIC_CATEGORIES,
    NicheCandidate,
    ScoutConfigError,
    brainstorm_packet,
    candidates_from_output,
    format_added,
    ingest,
    load_rpm_tiers,
    load_seeds,
    prompt_paths,
    propose_from_brainstorm,
    propose_from_seeds,
    slugify,
    step_ids,
)
from ytscout.settings import find_repo_root
from ytscout.store import connect, repo

SRC_ROOT = find_repo_root(Path(__file__).parent)
RPM_PATH = SRC_ROOT / RPM_TIERS_RELPATH
SEEDS_PATH = SRC_ROOT / SEEDS_RELPATH
PROMPT, SCHEMA = prompt_paths(SRC_ROOT)
STEP_IDS = step_ids(SRC_ROOT)
FAKE_CLAUDE_TEMPLATE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "session_id": "canned",
    "total_cost_usd": 0.05,
    "usage": {"input_tokens": 900, "output_tokens": 400},
}


def niche(topic: str, **overrides: object) -> dict:
    base = {
        "format": "shorts",
        "topic": topic,
        "label": f"Label for {topic}",
        "topic_category": "history_science",
        "example_search_queries": [f"{topic} one", f"{topic} two", f"{topic} three"],
        "why_ai_able": "stock footage and a script",
        "suspected_manual_steps": ["visuals_stock"],
        "evergreen": True,
        "faceless_ok": True,
    }
    base.update(overrides)
    return base


def canned_output(niches: list[dict]) -> str:
    return json.dumps({**FAKE_CLAUDE_TEMPLATE, "structured_output": {"niches": niches}}) + "\n"


def candidate(topic: str, **overrides: object) -> NicheCandidate:
    raw = niche(topic, **overrides)
    return NicheCandidate(
        format=str(raw["format"]),
        topic=str(raw["topic"]),
        label=str(raw["label"]),
        topic_category=str(raw["topic_category"]),
        example_search_queries=tuple(raw["example_search_queries"]),  # type: ignore[arg-type]
        why_ai_able=str(raw["why_ai_able"]),
        suspected_manual_steps=tuple(raw["suspected_manual_steps"]),  # type: ignore[arg-type]
        evergreen=bool(raw["evergreen"]),
        faceless_ok=bool(raw["faceless_ok"]),
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()


# --- rpm_tiers.yaml -------------------------------------------------------------------------


def test_rpm_table_has_14_categories_x_2_formats_every_row_sourced() -> None:
    table = load_rpm_tiers(RPM_PATH)
    assert table.category_ids == TOPIC_CATEGORIES
    assert len(table.categories) == 14
    for cat in table.categories.values():
        assert set(cat.rows) == set(FORMATS) == {"shorts", "longform"}
        assert cat.label
        for row in cat.rows.values():
            assert row.source, f"{cat.id} has no source"
            assert row.source == "estimate" or row.source.startswith("https://")
            assert row.last_reviewed == "2026-09"
            assert 0 <= row.low <= row.mid <= row.high
        # Shorts pay a small fraction of long-form in every category (DESIGN.md §6.3).
        assert cat.rows["shorts"].mid < cat.rows["longform"].mid / 10
        assert cat.rows["shorts"].high <= 0.32


def test_rpm_table_estimates_say_where_they_came_from() -> None:
    table = load_rpm_tiers(RPM_PATH)
    for cat in table.categories.values():
        for fmt, row in cat.rows.items():
            if row.source == "estimate":
                assert row.notes, f"{cat.id}.{fmt} is an estimate with no notes"
    # The ordering the design asks for: finance high, entertainment/animals low.
    longform_mid = {c: table.row(c, "longform").mid for c in TOPIC_CATEGORIES}
    assert longform_mid["finance_business"] == max(longform_mid.values())
    assert longform_mid["kids_family"] == min(longform_mid.values())
    assert longform_mid["entertainment_pop"] < longform_mid["education_explainer"]
    assert longform_mid["animals_nature"] < longform_mid["education_explainer"]


def test_schema_enum_matches_the_fixed_category_list() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    item = schema["properties"]["niches"]["items"]
    assert tuple(item["properties"]["topic_category"]["enum"]) == TOPIC_CATEGORIES
    assert item["properties"]["format"]["enum"] == list(FORMATS)
    queries = item["properties"]["example_search_queries"]
    assert (queries["minItems"], queries["maxItems"]) == (QUERIES_PER_NICHE, QUERIES_PER_NICHE)
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {
        "format",
        "topic",
        "label",
        "topic_category",
        "example_search_queries",
        "why_ai_able",
        "suspected_manual_steps",
        "evergreen",
        "faceless_ok",
    }


def _write_rpm(tmp_path: Path, mutate) -> Path:
    doc = yaml.safe_load(RPM_PATH.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "rpm.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_rpm_loader_rejects_a_missing_category(tmp_path: Path) -> None:
    path = _write_rpm(tmp_path, lambda d: d["categories"].pop("gaming"))
    with pytest.raises(ScoutConfigError, match=r"missing \['gaming'\]"):
        load_rpm_tiers(path)


def test_rpm_loader_rejects_an_extra_category(tmp_path: Path) -> None:
    def mutate(d: dict) -> None:
        d["categories"]["crypto"] = d["categories"]["gaming"]

    with pytest.raises(ScoutConfigError, match=r"unknown \['crypto'\]"):
        load_rpm_tiers(_write_rpm(tmp_path, mutate))


def test_rpm_loader_rejects_a_missing_format_or_source_or_bad_order(tmp_path: Path) -> None:
    def no_shorts(d: dict) -> None:
        d["categories"]["gaming"].pop("shorts")

    def no_source(d: dict) -> None:
        d["categories"]["gaming"]["shorts"]["source"] = ""

    def bad_order(d: dict) -> None:
        d["categories"]["gaming"]["shorts"]["usd_rpm"] = {"low": 5, "mid": 1, "high": 9}

    def bad_month(d: dict) -> None:
        d["categories"]["gaming"]["shorts"]["last_reviewed"] = "2026-13"

    for mutate, message in (
        (no_shorts, "missing format 'shorts'"),
        (no_source, "source is required"),
        (bad_order, "low <= mid <= high"),
        (bad_month, "last_reviewed must be YYYY-MM"),
    ):
        with pytest.raises(ScoutConfigError, match=message):
            load_rpm_tiers(_write_rpm(tmp_path, mutate))


# --- seed_niches.yaml ------------------------------------------------------------------------


def test_seeds_load_and_cross_validate_against_rpm_and_steps() -> None:
    seeds = load_seeds(SEEDS_PATH)
    table = load_rpm_tiers(RPM_PATH)
    assert len(seeds) == 5
    first = seeds[0]
    assert (first.format, first.topic) == ("shorts", "top5-countdown-dangerous-animals")
    assert first.topic_category == "animals_nature"
    keys = set()
    for seed in seeds:
        assert seed.topic_category in table.categories
        assert set(seed.suspected_manual_steps) <= STEP_IDS
        assert len(seed.example_search_queries) == QUERIES_PER_NICHE
        assert seed.format in FORMATS
        keys.add(seed.key)
    assert len(keys) == 5
    assert {s.format for s in seeds} == {"shorts", "longform"}


def _write_seeds(tmp_path: Path, mutate) -> Path:
    doc = yaml.safe_load(SEEDS_PATH.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "seeds.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_seed_loader_rejects_bad_entries(tmp_path: Path) -> None:
    def two_queries(d: dict) -> None:
        d["niches"][0]["example_search_queries"] = ["a", "b"]

    def bad_category(d: dict) -> None:
        d["niches"][0]["topic_category"] = "crypto"

    def bad_format(d: dict) -> None:
        d["niches"][0]["format"] = "vertical"

    def duplicate(d: dict) -> None:
        d["niches"].append(dict(d["niches"][0]))

    for mutate, message in (
        (two_queries, "exactly 3"),
        (bad_category, "unknown topic_category 'crypto'"),
        (bad_format, "format must be one of"),
        (duplicate, "duplicate seed"),
    ):
        with pytest.raises(ScoutConfigError, match=message):
            load_seeds(_write_seeds(tmp_path, mutate))


def test_slugify_normalises_topics() -> None:
    assert slugify("Top 5 Dangerous Animals") == "top-5-dangerous-animals"
    assert slugify("  history--mysteries_ranked ") == "history-mysteries-ranked"


def test_seeds_are_idempotent(conn: sqlite3.Connection) -> None:
    first = propose_from_seeds(conn, repo_root=SRC_ROOT)
    assert len(first.added) == 5 and first.skipped == 0 and first.source == "seed"
    second = propose_from_seeds(conn, repo_root=SRC_ROOT)
    assert len(second.added) == 0 and second.skipped_duplicate == 5
    rows = repo.list_niches(conn)
    assert len(rows) == 5
    assert {r["status"] for r in rows} == {"proposed"}
    assert {r["source"] for r in rows} == {"seed"}
    ref = rows[0]
    assert (ref["format"], ref["topic"]) == ("shorts", "top5-countdown-dangerous-animals")
    assert json.loads(ref["queries_json"]) == [
        "top 5 most dangerous animals shorts",
        "deadliest animals countdown",
        "most venomous animals ranked",
    ]
    assert json.loads(ref["required_steps_json"]) == ["visuals_stock", "qa"]
    meta = json.loads(ref["meta_json"])
    assert meta["evergreen"] is True and meta["faceless_ok"] is True
    assert "prompt_hash" not in meta  # seeds are not a Claude analysis


# --- ingest ------------------------------------------------------------------------------------


def test_ingest_counts_duplicates_bad_categories_and_bad_steps(conn: sqlite3.Connection) -> None:
    with conn:
        repo.insert_niche(
            conn,
            fmt="shorts",
            topic="already-there",
            topic_category="gaming",
            label="Already",
            source="seed",
            queries=["a", "b", "c"],
            required_steps=[],
        )
    batch = [
        candidate("fresh-one"),
        candidate("already-there"),  # duplicate of the DB row
        candidate("fresh-one", label="same key again"),  # duplicate within the batch
        candidate("fresh-one", format="longform"),  # same topic, other format: distinct
        candidate("bad-cat", topic_category="crypto"),
        candidate("bad-step", suspected_manual_steps=["visuals_stock", "colour_grade"]),
        candidate("bad-both", topic_category="crypto", suspected_manual_steps=["nope"]),
    ]
    result = ingest(
        conn, batch, source="llm", categories=set(TOPIC_CATEGORIES), known_steps=STEP_IDS
    )
    assert result.candidates == 7
    assert [(a.format, a.topic) for a in result.added] == [
        ("shorts", "fresh-one"),
        ("longform", "fresh-one"),
    ]
    assert result.skipped_duplicate == 2
    assert result.skipped_category == 2  # bad-both counts once, as a category skip
    assert result.skipped_steps == 1
    assert result.unknown_categories == ["crypto", "crypto"]
    assert result.unknown_steps == ["colour_grade"]
    assert result.skipped == 5
    assert len(repo.list_niches(conn)) == 3
    table = format_added(result)
    assert table.splitlines()[0].split() == ["id", "format", "topic", "category", "label"]
    assert "longform  fresh-one" in table


def test_insert_niche_rejects_unknown_format_and_source(conn: sqlite3.Connection) -> None:
    kwargs = dict(
        topic="t", topic_category="gaming", label="L", queries=["a", "b", "c"], required_steps=[]
    )
    with pytest.raises(ValueError, match="format"):
        repo.insert_niche(conn, fmt="vertical", source="seed", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source"):
        repo.insert_niche(conn, fmt="shorts", source="manual", **kwargs)  # type: ignore[arg-type]
    with conn:
        repo.insert_niche(conn, fmt="shorts", source="seed", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_niche(conn, fmt="shorts", source="llm", **kwargs)  # type: ignore[arg-type]


# --- the packet and the brainstorm -----------------------------------------------------------


def test_brainstorm_packet_carries_steps_coverage_categories_and_existing_niches(
    conn: sqlite3.Connection,
) -> None:
    propose_from_seeds(conn, repo_root=SRC_ROOT)
    packet = brainstorm_packet(conn, repo_root=SRC_ROOT, count=12)
    assert packet["count"] == 12 and packet["meta"]["count"] == 12
    assert [c["id"] for c in packet["topic_categories"]] == list(TOPIC_CATEGORIES)
    steps = {s["id"]: s for s in packet["production_steps"]}
    assert set(steps) == STEP_IDS
    assert steps["assembly"]["pipeline_coverage"] == "automated"
    assert steps["research"]["pipeline_coverage"] == "partial"
    assert steps["research"]["manual_hours_override"] == 0.2
    assert steps["presenter"]["disqualifying_for_faceless"] is True
    assert steps["visuals_stock"]["specific_footage_hours"] == 2.5
    assert len(packet["existing_niches"]) == 5
    assert packet["existing_niches"][0]["label"] == "Top 5 countdowns: dangerous animals"
    assert packet["reference_niche"]["topic"] == "top5-countdown-dangerous-animals"
    assert set(packet["constraints"]) >= {"faceless", "language", "evergreen", "formats"}
    assert packet["formats_pipeline_supports"] == ["shorts", "longform"]
    # Small: one brainstorm a week must not cost a big packet.
    assert len(json.dumps(packet).encode("utf-8")) < 20_000


def test_brainstorm_adds_new_rows_and_counts_the_duplicate_and_bad_step(
    conn: sqlite3.Connection, fake_claude, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    propose_from_seeds(conn, repo_root=SRC_ROOT)
    output = [
        niche("engineering-disasters-ranked", topic_category="history_science"),
        niche("one-animal-deep-dive", format="longform"),  # a seed already in the DB
        niche("budget-travel-hacks", topic_category="lifestyle_travel", format="longform"),
        niche("colour-graded-thing", suspected_manual_steps=["colour_grade"]),
    ]
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned_output(output))
    result = propose_from_brainstorm(
        conn, repo_root=SRC_ROOT, packets_dir=tmp_path / "packets", count=4, model="fake-model"
    )
    assert result.failure is None
    assert result.candidates == 4
    assert [a.topic for a in result.added] == [
        "engineering-disasters-ranked",
        "budget-travel-hacks",
    ]
    assert (result.skipped_duplicate, result.skipped_category, result.skipped_steps) == (1, 0, 1)
    assert result.unknown_steps == ["colour_grade"]
    assert result.prompt_hash == file_hash(PROMPT) and result.schema_hash == file_hash(SCHEMA)
    assert result.packet_path is not None and result.packet_path.name.endswith(
        "-niche_brainstorm-1.json"
    )
    assert result.usage == {"input_tokens": 900, "output_tokens": 400}

    rows = {r["topic"]: r for r in repo.list_niches(conn)}
    assert len(rows) == 7
    row = rows["budget-travel-hacks"]
    assert (row["format"], row["source"], row["status"]) == ("longform", "llm", "proposed")
    assert row["topic_category"] == "lifestyle_travel"
    meta = json.loads(row["meta_json"])
    assert meta["prompt_hash"] == file_hash(PROMPT)
    assert meta["schema_hash"] == file_hash(SCHEMA)
    assert meta["packet_path"] == str(result.packet_path)
    assert meta["why_ai_able"] == "stock footage and a script"
    assert json.loads(row["required_steps_json"]) == ["visuals_stock"]

    # The call itself: the rules the runner must keep, and our packet on the last line.
    record = fake_claude.record()
    argv = record["argv"]
    assert "--bare" not in argv
    assert argv[argv.index("--model") + 1] == "fake-model"
    assert argv[argv.index("--allowedTools") + 1] == "Read"
    assert argv[1].endswith(str(result.packet_path.resolve()))
    assert Path(record["cwd"]).resolve() == SRC_ROOT.resolve()
    packet = json.loads(result.packet_path.read_text(encoding="utf-8"))
    assert packet["count"] == 4 and len(packet["existing_niches"]) == 5


def test_brainstorm_accepts_the_fakes_generated_output(
    conn: sqlite3.Connection, fake_claude, tmp_path: Path
) -> None:
    """The schema-derived fake output validates; its step id 'fake' is unknown, so skipped."""
    result = propose_from_brainstorm(conn, repo_root=SRC_ROOT, packets_dir=tmp_path, count=1)
    assert result.failure is None
    assert result.candidates == 1 and result.added == [] and result.skipped_steps == 1


def test_schema_rejects_a_bad_category_before_anything_is_written(
    conn: sqlite3.Connection, fake_claude, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The enum is enforced at the runner: an unknown category fails the whole call."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    with pytest.raises(SchemaError, match="topic_category"):
        validate({"niches": [niche("x-y", topic_category="crypto")]}, schema)
    monkeypatch.setenv(
        "FAKE_CLAUDE_OUTPUT",
        canned_output([niche("good-one"), niche("bad-one", topic_category="crypto")]),
    )
    result = propose_from_brainstorm(conn, repo_root=SRC_ROOT, packets_dir=tmp_path, count=2)
    assert result.failure is not None and "does not match the schema" in result.failure
    assert result.added == [] and repo.list_niches(conn) == []


def test_brainstorm_failure_writes_nothing(
    conn: sqlite3.Connection, fake_claude, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    monkeypatch.setenv("FAKE_CLAUDE_STDERR", "boom")
    result = propose_from_brainstorm(conn, repo_root=SRC_ROOT, packets_dir=tmp_path, count=3)
    assert result.failure is not None and "exited 1" in result.failure
    assert result.packet_path is not None and result.packet_path.is_file()
    assert repo.list_niches(conn) == []


def test_candidates_from_output_normalises_topics() -> None:
    out = {"niches": [niche("Some-Topic", suspected_manual_steps=["qa", "qa"])]}
    (cand,) = candidates_from_output(out)
    assert cand.topic == "some-topic"
    assert cand.suspected_manual_steps == ("qa",)


# --- CLI -----------------------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp repo root with settings, the real config, prompts and schemas, and an empty DB."""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("own_channel_id: UCown\n", encoding="utf-8")
    for name in ("rpm_tiers.yaml", "seed_niches.yaml", "production_steps.yaml"):
        shutil.copy(SRC_ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copy(SRC_ROOT / "config" / "pipeline_coverage.yaml", tmp_path / "config")
    for sub in ("prompts", "schemas"):
        shutil.copytree(SRC_ROOT / sub, tmp_path / sub)
    for name in ("YT_API_KEY", "YT_CHANNEL_ID", "YT_CLIENT_SECRET_PATH", "YT_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _db_rows(root: Path, sql: str) -> list[tuple]:
    c = sqlite3.connect(root / "data" / "ytscout.sqlite")
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_cli_from_seeds_adds_five_then_zero(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scout", "propose", "--from-seeds"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "top5-countdown-dangerous-animals" in out
    assert "scout propose (seed): 5 candidate(s), 5 added, 0 duplicate(s) skipped" in out
    assert _db_rows(repo_root, "SELECT COUNT(*) FROM niches WHERE source = 'seed'") == [(5,)]

    assert main(["scout", "propose", "--from-seeds"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("added: none")
    assert "0 added, 5 duplicate(s) skipped" in out
    assert _db_rows(repo_root, "SELECT COUNT(*) FROM niches") == [(5,)]
    assert _db_rows(repo_root, "SELECT kind, status FROM runs") == [
        ("scout_propose_seeds", "ok"),
        ("scout_propose_seeds", "ok"),
    ]


def test_cli_from_seeds_works_without_settings(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo_root / "config" / "settings.yaml").unlink()
    assert main(["scout", "propose", "--from-seeds"]) == EXIT_OK
    assert "5 added" in capsys.readouterr().out


def test_cli_dry_runs_write_nothing(
    repo_root: Path, fake_claude, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scout", "propose", "--from-seeds", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "dry run: seeds" in out and "seeds: 5; would add: 5" in out
    assert main(["scout", "propose", "--dry-run", "--count", "7"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "asking for 7 niches; 0 already in the DB; 14 categories; 12 steps" in out
    assert "claude: ok" in out
    assert f"prompt: niche_brainstorm.md ({file_hash(PROMPT)})" in out
    assert fake_claude.record()["argv"] == ["--version"]
    assert not (repo_root / "data" / "packets").exists()
    assert not (repo_root / "data" / "ytscout.sqlite").exists()


def test_cli_brainstorm_adds_rows_and_prints_the_table(
    repo_root: Path,
    fake_claude,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["scout", "propose", "--from-seeds"]) == EXIT_OK
    capsys.readouterr()
    output = [
        niche("engineering-disasters-ranked"),
        niche("one-animal-deep-dive", format="longform"),
        niche("bad-step", suspected_manual_steps=["colour_grade"]),
    ]
    monkeypatch.setenv("FAKE_CLAUDE_OUTPUT", canned_output(output))
    assert main(["scout", "propose", "--count", "3"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "engineering-disasters-ranked" in out
    assert "unknown step ids skipped: colour_grade" in out
    assert (
        "scout propose (llm): 3 candidate(s), 1 added, 1 duplicate(s) skipped, "
        "0 with an unknown category, 1 with an unknown step id"
    ) in out
    assert "tokens 900 in / 400 out" in out
    assert _db_rows(repo_root, "SELECT COUNT(*) FROM niches WHERE source = 'llm'") == [(1,)]
    assert _db_rows(repo_root, "SELECT kind, status FROM runs ORDER BY id") == [
        ("scout_propose_seeds", "ok"),
        ("scout_propose_llm", "ok"),
    ]
    assert len(list((repo_root / "data" / "packets").glob("*-niche_brainstorm-*.json"))) == 1
    packet = json.loads(
        next((repo_root / "data" / "packets").glob("*-niche_brainstorm-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert packet["count"] == 3


def test_cli_brainstorm_default_count(repo_root: Path, fake_claude, capsys) -> None:
    assert main(["scout", "propose"]) == EXIT_OK
    assert "1 candidate(s), 0 added" in capsys.readouterr().out  # the fake fills one item
    packet_path = next((repo_root / "data" / "packets").glob("*.json"))
    assert json.loads(packet_path.read_text(encoding="utf-8"))["count"] == DEFAULT_COUNT


def test_cli_brainstorm_exits_5_without_claude(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(repo_root))
    assert main(["scout", "propose"]) == EXIT_CLAUDE_UNAVAILABLE
    assert "claude unavailable" in capsys.readouterr().err
    assert not (repo_root / "data" / "ytscout.sqlite").exists()


def test_cli_brainstorm_exits_5_when_the_call_fails(
    repo_root: Path,
    fake_claude,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "2")
    assert main(["scout", "propose"]) == EXIT_CLAUDE_UNAVAILABLE
    captured = capsys.readouterr()
    assert "claude failed" in captured.err and "nothing added" in captured.out
    assert _db_rows(repo_root, "SELECT COUNT(*) FROM niches") == [(0,)]
    assert _db_rows(repo_root, "SELECT kind, status FROM runs") == [("scout_propose_llm", "error")]


def test_cli_reports_a_broken_config(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rpm = repo_root / "config" / "rpm_tiers.yaml"
    doc = yaml.safe_load(rpm.read_text(encoding="utf-8"))
    doc["categories"].pop("gaming")
    rpm.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert main(["scout", "propose", "--from-seeds"]) == EXIT_ERROR
    assert "missing ['gaming']" in capsys.readouterr().err
    (repo_root / "config" / "seed_niches.yaml").unlink()
    assert main(["scout", "propose", "--from-seeds"]) == EXIT_ERROR
    assert "missing" in capsys.readouterr().err


def test_cli_rejects_unknown_flags(repo_root: Path) -> None:
    with pytest.raises(SystemExit):
        main(["scout", "propose", "--max-units", "5"])


def test_steps_file_is_the_one_the_packet_reads() -> None:
    assert {s.id for s in load_steps(SRC_ROOT / STEPS_RELPATH)} == STEP_IDS
