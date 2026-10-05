"""``fcrm_seed.gen_clauses``: the one-time synthetic corpus generator, driven by
a deterministic fake writer (there are no Bedrock credentials in unit tests).

What is proved: the plan equals the seed's declared corpus (nine sources, 197
clauses); the four seed clauses are kept verbatim; every clause has a provenance;
code-assigned fields do not depend on what the model wrote; the Markdown
segments back into the same clauses at the same lines; a bad batch is retried
once with the problems named and never accepted unchecked.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fcrm_context_worker.segmenter import segment_clauses
from fcrm_contracts.enums import ChangeType, Domain
from fcrm_seed import clause_model, gen_clauses
from fcrm_seed.clause_model import ClausesFile
from fcrm_seed.gen_clauses import (
    ClauseBatch,
    ClauseDraft,
    ClauseGenerationError,
    ClausePlanError,
    ClauseRequest,
    WriterOutputError,
    WrittenBatch,
    batch_problems,
    build_plan,
    draft_problems,
    generate_corpus,
)
from fcrm_seed.loader import load_raw, read_clauses
from pydantic import ValidationError

STAMP = "2026-10-05T10:00:00Z"
VERBATIM_IDS = {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}
FAKE_MODEL = "fake-writer"


def _draft_text(clause_id: str, flavour: str) -> str:
    return (
        f"The bank should maintain a documented control for item {clause_id}, "
        f"assign a named owner, record the evidence it relies on and review it "
        f"at a defined interval so that exceptions are escalated and decided "
        f"before the related change goes live. Variant {flavour}."
    )


def _draft(clause_id: str, ordinal: int, flavour: str = "a") -> ClauseDraft:
    return ClauseDraft(
        clause_id=clause_id,
        title=f"Control expectation {clause_id}",
        text=_draft_text(clause_id, flavour),
        risk_areas=[("customer", "product", "geography", "channel")[ordinal % 4]],
        trigger_types=[
            ("new_product", "new_geography", "vendor_onboarding")[ordinal % 3]
        ],
        jurisdictions=["US"],
    )


class _ScriptedWriter:
    """Answers each call from a list of callables (to script bad answers)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[ClauseRequest] = []

    async def write(self, request: ClauseRequest) -> WrittenBatch:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        drafts = answer(request)
        return WrittenBatch(batch=ClauseBatch(clauses=drafts), model_id=FAKE_MODEL)


def _good(request: ClauseRequest) -> list[ClauseDraft]:
    return [_draft(s.clause_id, s.ordinal) for s in request.slots]


def _generate(writer: Any, **kw: Any) -> gen_clauses.GeneratedCorpus:
    return asyncio.run(generate_corpus(load_raw(), writer, generated_at=STAMP, **kw))


# ----------------------------------------------------------------- the plan
def test_the_plan_matches_the_seed_declared_corpus() -> None:
    seed = load_raw()
    plans = build_plan(seed)
    declared = {s["id"]: s["clauses"] for s in seed["corpus"]["sources"]}
    assert {p.source_id: len(p.slots) for p in plans} == declared
    assert sum(declared.values()) == seed["corpus"]["clause_count"] == 197
    ids = [s.clause_id for p in plans for s in p.slots]
    assert len(ids) == len(set(ids)) == 197


def test_the_four_seed_clause_ids_fall_out_of_the_numbering_scheme() -> None:
    plans = build_plan(load_raw())
    verbatim = {s.clause_id for p in plans for s in p.slots if s.verbatim}
    assert verbatim == VERBATIM_IDS


def test_outline_section_labels_are_the_seed_section_names() -> None:
    seed = load_raw()
    for plan, source in zip(build_plan(seed), seed["corpus"]["sources"], strict=True):
        assert [s.label for s in plan.outline.sections] == source["sections"]


def test_tag_vocabularies_match_the_contract_enums() -> None:
    assert tuple(d.value for d in Domain) == clause_model.RISK_AREAS
    assert tuple(t.value for t in ChangeType) == clause_model.TRIGGER_TYPES


def _seed_with(change: Any) -> dict[str, Any]:
    seed = copy.deepcopy(load_raw())
    change(seed)
    return seed


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s["corpus"]["sources"][0].update(clauses=85), "outline has 86"),
        (
            lambda s: s["corpus"]["sources"][5]["sections"].reverse(),
            "outline sections",
        ),
        (lambda s: s["corpus"]["sources"][1].update(id="NOPE"), "no outline"),
        (
            lambda s: s["demo"]["clauses"][0].update(clause_id="FFIEC-XXX-99"),
            "without a slot",
        ),
        (lambda s: s["corpus"].update(clause_count=198), "plan has 197"),
    ],
    ids=["count", "sections", "source", "verbatim", "total"],
)
def test_a_plan_that_disagrees_with_the_seed_is_refused(
    change: Any, message: str
) -> None:
    with pytest.raises(ClausePlanError, match=message):
        build_plan(_seed_with(change))


# ------------------------------------------------------------ the generation
def test_generation_makes_197_unique_clauses_with_provenance_on_each(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    corpus = synthetic_corpus
    clauses = corpus.file.clauses
    assert len(clauses) == corpus.file.meta.clause_count == 197
    assert len({c.clause_id for c in clauses}) == 197
    per_source = Counter(c.source_id for c in clauses)
    declared = {s["id"]: s["clauses"] for s in load_raw()["corpus"]["sources"]}
    assert per_source == declared
    assert all(c.provenance.note and c.provenance.generated_by for c in clauses)
    assert all(c.quotable for c in clauses)


def test_provenance_kind_follows_the_source_kind(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    seed = load_raw()
    kinds = {s["id"]: s["kind"] for s in seed["corpus"]["sources"]}
    for clause in synthetic_corpus.file.clauses:
        if clause.clause_id in VERBATIM_IDS:
            expected = "seed_verbatim"
        elif kinds[clause.source_id] == "public":
            expected = "synthetic_public_stand_in"
        else:
            expected = "synthetic_internal_standard"
        assert clause.provenance.kind == expected, clause.clause_id
    counts = Counter(c.provenance.kind for c in synthetic_corpus.file.clauses)
    assert counts["seed_verbatim"] == 4
    assert counts["synthetic_public_stand_in"] == 122 - 2
    assert counts["synthetic_internal_standard"] == 75 - 2


def test_generated_by_is_the_writers_model_and_the_seed_for_verbatim(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    corpus = synthetic_corpus
    by_id = {c.clause_id: c for c in corpus.file.clauses}
    assert by_id["TP71-2.4"].provenance.generated_by == FAKE_MODEL
    assert by_id["TP71-2.3"].provenance.generated_by == "walkthrough_v2_seed"
    assert corpus.file.meta.generated_by == sorted({FAKE_MODEL, "walkthrough_v2_seed"})


def test_the_four_seed_clauses_are_kept_character_for_character(
    fake_clause_writer: Any,
) -> None:
    writer = fake_clause_writer()
    corpus = _generate(writer)
    by_id = {c.clause_id: c for c in corpus.file.clauses}
    for seeded in load_raw()["demo"]["clauses"]:
        clause = by_id[seeded["clause_id"]]
        assert clause.text == seeded["quote"]
        assert clause.ref == seeded["ref"]
        assert clause.title == seeded["title"]
        assert clause.source_id == seeded["src"]
    asked = {s.clause_id for r in writer.requests for s in r.slots}
    assert not asked & VERBATIM_IDS
    shown = {e.clause_id for r in writer.requests for e in r.already_in_section}
    assert shown >= VERBATIM_IDS


def test_each_batch_is_one_section_and_within_the_batch_size(
    fake_clause_writer: Any,
) -> None:
    writer = fake_clause_writer()
    _generate(writer, batch_size=4)
    assert max(len(r.slots) for r in writer.requests) == 4
    for request in writer.requests:
        prefixes = {s.clause_id.rsplit("-", 1)[0].split(".")[0] for s in request.slots}
        assert len(prefixes) == 1


def test_later_batches_see_earlier_titles_in_the_same_section(
    fake_clause_writer: Any,
) -> None:
    writer = fake_clause_writer()
    _generate(writer, batch_size=3)
    tppp = [r for r in writer.requests if r.section == "Third-Party Payment Processors"]
    assert len(tppp) > 1
    assert tppp[1].already_in_section
    assert any(e.clause_id == "FFIEC-TPPP-04" for e in tppp[0].already_in_section)


def test_refs_are_code_assigned_and_never_look_like_a_real_citation(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    seed = load_raw()
    kinds = {s["id"]: s["kind"] for s in seed["corpus"]["sources"]}
    by_id = {c.clause_id: c for c in synthetic_corpus.file.clauses}
    assert by_id["FFIEC-TPPP-05"].ref == (
        "FFIEC synthetic stand-in · Third-Party Payment Processors · item 05"
    )
    assert by_id["TP71-2.4"].ref == "Standard 7.1 §2.4 · v2025-11"
    for clause in by_id.values():
        if kinds[clause.source_id] == "public" and clause.clause_id not in VERBATIM_IDS:
            assert "synthetic stand-in" in clause.ref
            assert "§" not in clause.ref


def test_code_assigned_fields_do_not_depend_on_what_the_model_wrote(
    fake_clause_writer: Any,
) -> None:
    first = {c.clause_id: c for c in _generate(fake_clause_writer("a")).file.clauses}
    second = {c.clause_id: c for c in _generate(fake_clause_writer("b")).file.clauses}
    assert first.keys() == second.keys()
    fixed = ("source_id", "ref", "page", "line_start", "line_end", "quotable")
    for clause_id, clause in first.items():
        for name in fixed:
            assert getattr(clause, name) == getattr(second[clause_id], name)
    assert first["PR23-1.2"].text != second["PR23-1.2"].text


def test_a_rerun_with_the_same_writer_is_byte_identical(
    synthetic_corpus: gen_clauses.GeneratedCorpus, fake_clause_writer: Any
) -> None:
    one, two = synthetic_corpus, _generate(fake_clause_writer())
    assert gen_clauses.clauses_json(one.file) == gen_clauses.clauses_json(two.file)
    assert one.markdown == two.markdown


def test_page_is_the_fifty_line_virtual_page_of_the_clause_heading(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    for clause in synthetic_corpus.file.clauses:
        assert clause.page == (clause.line_start - 1) // 50 + 1
        assert clause.line_end == clause.line_start + 2


# --------------------------------------------------------------- the markdown
def test_the_markdown_segments_back_into_the_same_clauses_and_lines(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    corpus = synthetic_corpus
    by_source: dict[str, list[Any]] = {}
    for clause in corpus.file.clauses:
        by_source.setdefault(clause.source_id, []).append(clause)
    assert set(corpus.markdown) == set(by_source)
    for source_id, text in corpus.markdown.items():
        segments = [s for s in segment_clauses(text) if s.kind == "clause"]
        expected = by_source[source_id]
        assert [s.clause_id for s in segments] == [c.clause_id for c in expected]
        lines = text.split("\n")
        for segment, clause in zip(segments, expected, strict=True):
            assert segment.text == clause.text
            assert (segment.line_start, segment.line_end) == (
                clause.line_start,
                clause.line_end,
            )
            assert lines[clause.line_start - 1].startswith(f"### {clause.clause_id} ")


def test_section_headings_never_parse_as_clauses(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    corpus = synthetic_corpus
    sections = {s.section for t in corpus.markdown.values() for s in segment_clauses(t)}
    assert "Section 1 Scope" in sections
    assert "Third-Party Payment Processors" in sections
    assert "50% rule" in sections


def test_every_markdown_file_says_it_is_synthetic(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    for text in synthetic_corpus.markdown.values():
        assert "Synthetic" in text.split("\n")[2]
        assert text.endswith("\n")


# ---------------------------------------------------------------- validation
def _good_draft(**over: Any) -> ClauseDraft:
    return _draft("OFAC-1.1", 1).model_copy(update=over)


def test_a_clean_draft_has_no_problems() -> None:
    assert draft_problems("public", _good_draft()) == []
    assert draft_problems("synthetic", _good_draft()) == []


@pytest.mark.parametrize(
    ("kind", "text", "needle"),
    [
        ("public", "x " * 40 + "per 12 CFR it applies", "real citation"),
        ("public", "x " * 40 + "as updated in 2024 by guidance", "real citation"),
        ("public", "x " * 40 + "see §3 for the rest", "section sign"),
        ("synthetic", "x " * 40 + "cash pickup agents", "cash-pickup"),
        ("public", "x " * 40 + "a cash-out channel", "cash-pickup"),
        ("synthetic", "1. " + "x " * 40, "open with a letter"),
        ("synthetic", "x " * 40 + "line\nbreak", "one paragraph"),
        ("synthetic", "short " * 10, "words"),
    ],
)
def test_draft_rules_reject_what_the_prompt_forbids(
    kind: str, text: str, needle: str
) -> None:
    draft = _good_draft().model_copy(update={"text": text})
    assert any(needle in p for p in draft_problems(kind, draft))


def test_internal_standards_may_refer_to_their_own_section_signs() -> None:
    draft = _good_draft().model_copy(
        update={"text": _draft_text("TP71-1.1", "see §2.3 of this standard")}
    )
    assert draft_problems("synthetic", draft) == []


def test_the_schema_is_strict_about_tags_and_jurisdictions() -> None:
    base = _good_draft().model_dump()
    for field, value in (
        ("risk_areas", ["weather"]),
        ("trigger_types", []),
        ("jurisdictions", ["usa"]),
        ("text", "too short"),
    ):
        with pytest.raises(ValidationError):
            ClauseDraft.model_validate({**base, field: value})
    with pytest.raises(ValidationError):
        ClauseDraft.model_validate({**base, "surprise": 1})


def test_ids_must_be_echoed_exactly_and_in_order() -> None:
    request = ClauseRequest(
        source_id="OFAC",
        source_title="t",
        source_kind="public",
        used_for="u",
        section="s",
        slots=[
            {"clause_id": "OFAC-1.1", "ordinal": 1},
            {"clause_id": "OFAC-1.3", "ordinal": 3},
        ],
    )
    wrong = ClauseBatch(clauses=[_draft("OFAC-1.3", 3), _draft("OFAC-1.1", 1)])
    assert "clause ids must be exactly" in batch_problems(request, wrong)[0]
    right = ClauseBatch(clauses=[_draft("OFAC-1.1", 1), _draft("OFAC-1.3", 3)])
    assert batch_problems(request, right) == []


# --------------------------------------------------------------------- retry
def _wrong_ids(request: ClauseRequest) -> list[ClauseDraft]:
    return [_draft(f"WRONG-{s.ordinal}", s.ordinal) for s in request.slots]


def _cash(request: ClauseRequest) -> list[ClauseDraft]:
    bad = _draft("X", 1).text.replace("control", "cash pickup control")
    return [
        _draft(s.clause_id, s.ordinal).model_copy(update={"text": bad})
        for s in request.slots
    ]


def test_a_rejected_batch_is_retried_once_with_the_problems_named() -> None:
    writer = _ScriptedWriter(_wrong_ids, _good)
    corpus = _generate(writer)
    assert len(corpus.file.clauses) == 197
    first, second = writer.requests[:2]
    assert first.feedback is None
    assert second.feedback is not None
    assert "clause ids must be exactly" in second.feedback
    assert [s.clause_id for s in first.slots] == [s.clause_id for s in second.slots]


def test_a_batch_that_stays_invalid_stops_the_run() -> None:
    writer = _ScriptedWriter(_cash)
    with pytest.raises(ClauseGenerationError, match="after 2 attempts"):
        _generate(writer)
    assert len(writer.requests) == 2


def test_an_unparseable_answer_is_retried_like_any_other_rejection() -> None:
    writer = _ScriptedWriter(WriterOutputError("no tool call"), _good)
    corpus = _generate(writer)
    assert len(corpus.file.clauses) == 197
    assert "could not be parsed" in (writer.requests[1].feedback or "")


def test_the_attempt_limit_is_configurable() -> None:
    writer = _ScriptedWriter(_wrong_ids)
    with pytest.raises(ClauseGenerationError, match="after 3 attempts"):
        _generate(writer, max_attempts=3)
    assert len(writer.requests) == 3


# ------------------------------------------------------------ files and loader
def test_the_written_files_round_trip_through_the_loader(
    tmp_path: Path, synthetic_corpus: gen_clauses.GeneratedCorpus
) -> None:
    corpus = synthetic_corpus
    out, folder = tmp_path / "clauses.json", tmp_path / "corpus"
    gen_clauses.write_outputs(corpus, out, folder)
    loaded = read_clauses(out)
    assert loaded is not None
    assert len(loaded) == 197
    assert loaded[0]["provenance"]["kind"] in {
        "synthetic_public_stand_in",
        "seed_verbatim",
    }
    assert sorted(p.stem for p in folder.glob("*.md")) == sorted(corpus.markdown)


def test_a_missing_clauses_file_is_none_not_an_error(tmp_path: Path) -> None:
    assert read_clauses(tmp_path / "absent.json") is None


def test_a_truncated_or_edited_clauses_file_fails_loudly(
    tmp_path: Path, synthetic_corpus: gen_clauses.GeneratedCorpus
) -> None:
    good = json.loads(gen_clauses.clauses_json(synthetic_corpus.file))
    first = good["clauses"][0]
    path = tmp_path / "clauses.json"
    for broken in (
        {**good, "clauses": good["clauses"][:5]},
        {**good, "clauses": [{**first, "extra": 1}, *good["clauses"][1:]]},
        {**good, "clauses": [first, first, *good["clauses"][2:]]},
    ):
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValidationError):
            read_clauses(path)


def test_clauses_file_rejects_duplicate_ids(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    file = synthetic_corpus.file
    meta = file.meta.model_copy(update={"clause_count": 2})
    with pytest.raises(ValidationError, match="duplicate clause ids"):
        ClausesFile(meta=meta, clauses=[file.clauses[0], file.clauses[0]])


def test_meta_records_the_prompt_version_and_hash(
    synthetic_corpus: gen_clauses.GeneratedCorpus,
) -> None:
    meta = synthetic_corpus.file.meta
    assert meta.prompt_version == gen_clauses.PROMPT_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", meta.prompt_sha256)
    assert meta.corpus_version == "v14"
    assert meta.generated_at == STAMP


# --------------------------------------------------------------------- the CLI
def test_the_cli_writes_both_outputs_with_a_fake_writer(
    tmp_path: Path, fake_clause_writer: Any
) -> None:
    code = gen_clauses.main(
        ["--out", str(tmp_path / "c.json"), "--corpus-dir", str(tmp_path / "corpus")],
        writer_factory=fake_clause_writer,
    )
    assert code == 0
    assert len(read_clauses(tmp_path / "c.json") or []) == 197
    assert len(list((tmp_path / "corpus").glob("*.md"))) == 9


def test_the_cli_exits_non_zero_when_generation_fails(tmp_path: Path) -> None:
    code = gen_clauses.main(
        ["--out", str(tmp_path / "c.json"), "--corpus-dir", str(tmp_path / "corpus")],
        writer_factory=lambda: _ScriptedWriter(_wrong_ids),
    )
    assert code == 1
    assert not (tmp_path / "c.json").exists()


def test_the_default_output_paths_are_next_to_seed_json() -> None:
    from fcrm_seed.loader import clauses_path, corpus_dir

    assert clauses_path().parent == corpus_dir().parent
    assert clauses_path().name == "clauses.json"
    assert corpus_dir().name == "corpus"


# ------------------------------------------------- the live writer, offline
def _function_model_agent(fail_first: bool = False) -> Any:
    from pydantic_ai import Agent, ToolOutput
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    calls = {"n": 0}

    def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if fail_first and calls["n"] == 1:
            return ModelResponse(parts=[TextPart(content="no tool call")])
        prompt = "\n".join(
            str(part.content)
            for m in messages
            for part in getattr(m, "parts", [])
            if part.__class__.__name__ == "UserPromptPart"
        )
        listed = prompt.split("echoing each clause_id:\n")[1].split("\n")
        ids = [ln[2:] for ln in listed if re.fullmatch(r"- [A-Za-z0-9.-]+", ln)]
        drafts = [_draft(i, n).model_dump() for n, i in enumerate(ids)]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name, args={"clauses": drafts}
                )
            ]
        )

    return Agent(
        FunctionModel(respond, model_name="function-model"),
        output_type=ToolOutput(ClauseBatch, name="clause_batch"),
        instructions=gen_clauses.SYSTEM_PROMPT,
        retries=0,
    )


def test_the_agent_writer_runs_one_request_and_reports_the_model() -> None:
    writer = gen_clauses.AgentClauseWriter(_function_model_agent(), "fallback-id")
    corpus = _generate(writer, batch_size=8)
    assert len(corpus.file.clauses) == 197
    assert "function-model" in corpus.file.meta.generated_by


def test_an_agent_answer_without_the_output_tool_is_a_retryable_rejection() -> None:
    writer = gen_clauses.AgentClauseWriter(_function_model_agent(True), "fallback-id")
    corpus = _generate(writer, batch_size=8)
    assert len(corpus.file.clauses) == 197


def test_the_user_prompt_lists_slots_context_and_feedback() -> None:
    request = ClauseRequest(
        source_id="INT-TP-7.1",
        source_title="Internal Third-Party AML Standard 7.1",
        source_kind="synthetic",
        used_for="Vendor and agent controls",
        section="§2 Payout agents",
        slots=[{"clause_id": "TP71-2.4", "ordinal": 4}],
        already_in_section=[
            {"clause_id": "TP71-2.3", "title": "t", "text": "verbatim text"}
        ],
        feedback="clause ids must be exactly ['TP71-2.4']",
    )
    prompt = gen_clauses.render_user_prompt(request)
    assert "INTERNAL standard" in prompt
    assert "- TP71-2.4" in prompt
    assert "TP71-2.3: t: verbatim text" in prompt
    assert "previous answer was rejected" in prompt


def test_no_model_id_is_written_in_the_generator() -> None:
    for module in (gen_clauses, clause_model):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        for line in source.splitlines():
            assert not re.search(r"claude-|anthropic\.|amazon\.nova|gpt-\d", line, re.I)


def test_the_default_ai_root_is_the_repos_ai_folder_with_tiers_yaml() -> None:
    assert (gen_clauses.DEFAULT_AI_ROOT / "models" / "tiers.yaml").is_file()


def test_the_live_writer_builds_the_mid_tier_from_tiers_yaml_without_a_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fcrm_agents.models import load_tiers_config, tier_models
    from fcrm_contracts.enums import Tier

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    writer = gen_clauses.build_live_writer()
    mid = tier_models(Tier.MID, load_tiers_config(gen_clauses.DEFAULT_AI_ROOT))
    assert isinstance(writer, gen_clauses.AgentClauseWriter)
    assert writer._fallback == mid.primary
    model = writer._agent.model
    assert type(model).__name__ == "FallbackModel"
    assert len(model.models) == len(mid.chain)
