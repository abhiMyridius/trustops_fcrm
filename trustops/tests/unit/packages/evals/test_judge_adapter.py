"""The judge: its settings from ``tiers.yaml``, the Bedrock adapter against a
fake client (no network), the rubric judge behind ``narrative_faithfulness``,
the DeepEval metric wrappers and the texts a judge reads."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals.context import EvalContext
from fcrm_evals.datasets import load
from fcrm_evals.metrics.judge import (
    NARRATIVE_FAITHFULNESS,
    JudgeContext,
    JudgeRequest,
    JudgeUnavailableError,
    RubricJudgeMetric,
    judge_metric_spec,
)
from fcrm_evals.outputs import AgentOutput, decode_items
from fcrm_evals.usage import PriceTable, UsageMeter

pytest.importorskip("deepeval")
pytest.importorskip("aiobotocore")

from fcrm_evals.judge.cases import context_lines, judge_case  # noqa: E402
from fcrm_evals.judge.deepeval_metrics import (  # noqa: E402
    ANSWER_RELEVANCY,
    FAITHFULNESS,
    HALLUCINATION_FREE,
    THRESHOLDS,
    DeepEvalJudge,
    deepeval_factory,
)
from fcrm_evals.judge.llm import (  # noqa: E402
    BedrockJudgeLLM,
    JudgeConfigError,
    JudgeSettings,
)
from fcrm_evals.judge.rubric import BedrockRubricJudge, RubricVerdict  # noqa: E402
from fcrm_evals.live import RowPackBuilder  # noqa: E402

VERDICT = json.dumps(
    {"traceable": 1.0, "no_invented_numbers": 1.0, "emphasis": 1.0, "reasoning": "ok"}
)


@pytest.fixture
def settings(tiers_config: Any) -> JudgeSettings:
    return JudgeSettings.from_tiers(tiers_config, region="us-east-1")


@pytest.fixture
def judge_llm(
    settings: JudgeSettings,
    price_table: PriceTable,
    fake_converse: Callable[..., Any],
) -> Callable[..., tuple[BedrockJudgeLLM, Any, UsageMeter]]:
    def build(*replies: Any) -> tuple[BedrockJudgeLLM, Any, UsageMeter]:
        client, factory = fake_converse(*replies)
        meter = UsageMeter(price_table)
        return BedrockJudgeLLM(settings, meter, client_factory=factory), client, meter

    return build


def test_the_judge_runs_on_the_judge_tier_not_the_assessors(
    tiers_config: Any, settings: JudgeSettings
) -> None:
    tiers = tiers_config["tiers"]
    assert settings.model_id == tiers["judge"]["primary"]
    assert settings.model_id != tiers["strong"]["primary"]
    assert settings.max_tokens == tiers["judge"]["max_tokens"]
    price = tiers_config["prices_usd_per_mtok"]["rows"][settings.model_id]
    assert settings.input_usd_per_token == pytest.approx(price["input"] / 1e6)
    assert settings.output_usd_per_token == pytest.approx(price["output"] / 1e6)


def test_settings_refuse_a_file_without_a_judge_tier_or_its_price(
    tiers_config: Any,
) -> None:
    no_tier = {**tiers_config, "tiers": {"cheap": tiers_config["tiers"]["cheap"]}}
    with pytest.raises(JudgeConfigError, match="no 'judge' tier"):
        JudgeSettings.from_tiers(no_tier, region="us-east-1")
    no_price = {**tiers_config, "prices_usd_per_mtok": {"rows": {}}}
    with pytest.raises(JudgeConfigError, match="no row"):
        JudgeSettings.from_tiers(no_price, region="us-east-1")


def test_the_region_comes_from_the_environment(
    tiers_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    assert JudgeSettings.from_tiers(tiers_config).region == "eu-west-1"


async def test_a_call_sends_only_a_token_cap_and_is_metered(
    judge_llm: Callable[..., Any], settings: JudgeSettings
) -> None:
    llm, client, meter = judge_llm("a reply")
    text, cost = await llm.a_generate("grade this")
    assert text == "a reply"
    request = client.requests[0]
    assert request["modelId"] == settings.model_id
    assert request["inferenceConfig"] == {"maxTokens": settings.max_tokens}
    assert request["messages"][0]["content"] == [{"text": "grade this"}]
    assert len(meter.calls) == 1
    assert (meter.input_tokens, meter.output_tokens) == (1000, 200)
    expected = 1000 * settings.input_usd_per_token + 200 * settings.output_usd_per_token
    assert float(cost) == pytest.approx(expected)
    assert meter.cost_usd == pytest.approx(expected)


async def test_a_schema_turns_the_reply_into_that_model(
    judge_llm: Callable[..., Any],
) -> None:
    llm, _client, _meter = judge_llm(f"Here you go: {VERDICT}")
    verdict, _cost = await llm.a_generate("grade this", schema=RubricVerdict)
    assert verdict == RubricVerdict(
        traceable=1.0, no_invented_numbers=1.0, emphasis=1.0, reasoning="ok"
    )


async def test_the_usage_is_reported_by_label_never_by_model_id(
    judge_llm: Callable[..., Any], settings: JudgeSettings
) -> None:
    llm, _client, meter = judge_llm("a reply")
    await llm.a_generate("grade this")
    usage = meter.as_usage()
    assert usage.models == ["Opus 5.5"]
    assert settings.model_id not in usage.model_dump_json()


async def test_the_rubric_prompt_comes_from_ai_prompts_and_holds_the_facts(
    judge_llm: Callable[..., Any], repo_ai: Path
) -> None:
    llm, _client, _meter = judge_llm()
    judge = BedrockRubricJudge(llm, ai_root=repo_ai)
    row = load(AgentName.NARRATIVE)[0]
    request = JudgeRequest(
        rubric=NARRATIVE_FAITHFULNESS,
        context=JudgeContext.from_row(row),
        output_text="Vendor diligence is the driver.",
        vote=0,
    )
    prompt = judge.prompt_for(request)
    assert "Vendor diligence is the driver." in prompt
    assert "composite_after: 10.4" in prompt
    assert "third-party channel" in prompt
    assert "{{" not in prompt


async def test_each_vote_is_one_call_and_the_real_judge_scores_a_row(
    judge_llm: Callable[..., Any], repo_ai: Path
) -> None:
    llm, client, _meter = judge_llm(VERDICT, VERDICT, VERDICT)
    metric = RubricJudgeMetric(
        "narrative_faithfulness",
        NARRATIVE_FAITHFULNESS,
        BedrockRubricJudge(llm, ai_root=repo_ai),
        threshold=0.9,
        k=3,
    )
    row = load(AgentName.NARRATIVE)[0]
    output = AgentOutput(
        row_id=row.row_id,
        producer="t",
        items=decode_items(row.agent, row.expected.reference_outputs),
    )
    spec = judge_metric_spec(metric)
    observation = await spec.fn(row, output, EvalContext())  # type: ignore[misc]
    assert observation is not None
    assert observation.score == 1.0
    assert len(client.requests) == 3


async def test_an_unparseable_verdict_is_an_unavailable_judge(
    judge_llm: Callable[..., Any], repo_ai: Path
) -> None:
    llm, _client, _meter = judge_llm("I cannot grade this")
    judge = BedrockRubricJudge(llm, ai_root=repo_ai)
    row = load(AgentName.NARRATIVE)[0]
    request = JudgeRequest(
        rubric=NARRATIVE_FAITHFULNESS,
        context=JudgeContext.from_row(row),
        output_text="text",
        vote=0,
    )
    with pytest.raises(JudgeUnavailableError):
        await judge(request)


async def test_an_unavailable_judge_leaves_the_row_not_measured(
    judge_llm: Callable[..., Any], repo_ai: Path
) -> None:
    llm, _client, _meter = judge_llm("nonsense", "nonsense", "nonsense")
    metric = RubricJudgeMetric(
        "narrative_faithfulness",
        NARRATIVE_FAITHFULNESS,
        BedrockRubricJudge(llm, ai_root=repo_ai),
        threshold=0.9,
    )
    row = load(AgentName.NARRATIVE)[0]
    output = AgentOutput(
        row_id=row.row_id,
        producer="t",
        items=decode_items(row.agent, row.expected.reference_outputs),
    )
    spec = judge_metric_spec(metric)
    assert await spec.fn(row, output, EvalContext()) is None  # type: ignore[misc]


class StubMetric:
    """A DeepEval metric that scores whatever it was told to."""

    def __init__(self, score: float, reason: str = "", boom: bool = False) -> None:
        self.score: float | None = None
        self.reason: str | None = reason
        self._score = score
        self._boom = boom

    async def a_measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        if self._boom:
            msg = "judge down"
            raise ValueError(msg)
        self.score = self._score
        return self._score


@pytest.fixture(scope="module")
def narrative_judge() -> Callable[..., DeepEvalJudge]:
    from fcrm_seed.loader import load_raw
    from fcrm_tools import build_seed_registry, seed_pins

    root = Path(__file__).parents[4] / "ai"
    packs = RowPackBuilder.from_ai(build_seed_registry(), seed_pins(load_raw()), root)

    def build(factory: Callable[[str], Any]) -> DeepEvalJudge:
        return DeepEvalJudge(packs, factory)

    return build


def _echo_output(agent: AgentName, row_index: int = 0) -> tuple[Any, AgentOutput]:
    row = load(agent)[row_index]
    items = decode_items(agent, row.expected.reference_outputs)
    return row, AgentOutput(row_id=row.row_id, producer="t", items=items)


async def test_a_judge_metric_is_the_mean_score_with_the_reason_on_a_miss(
    narrative_judge: Callable[..., DeepEvalJudge],
) -> None:
    scores = {FAITHFULNESS: 0.95, ANSWER_RELEVANCY: 0.5, HALLUCINATION_FREE: 1.0}
    judge = narrative_judge(lambda name: StubMetric(scores[name], f"{name} reason"))
    row, output = _echo_output(AgentName.NARRATIVE)
    by_name = {s.name: s for s in judge.specs(AgentName.NARRATIVE)}
    assert set(by_name) == set(THRESHOLDS)
    ok = await by_name[FAITHFULNESS].fn(row, output, EvalContext())  # type: ignore[misc]
    miss = await by_name[ANSWER_RELEVANCY].fn(row, output, EvalContext())  # type: ignore[misc]
    assert ok is not None
    assert (ok.score, ok.flags) == (0.95, [])
    assert miss is not None
    assert miss.score == 0.5
    assert miss.flags == ["answer_relevancy 0.50 < 0.80: answer_relevancy reason"]
    assert all(s.kind == "llm_judge" for s in by_name.values())


async def test_a_row_the_judge_cannot_grade_is_not_measured_and_counted(
    narrative_judge: Callable[..., DeepEvalJudge],
) -> None:
    judge = narrative_judge(lambda name: StubMetric(1.0, boom=True))
    row, output = _echo_output(AgentName.NARRATIVE)
    spec = judge.specs(AgentName.NARRATIVE)[0]
    assert await spec.fn(row, output, EvalContext()) is None  # type: ignore[misc]
    assert judge.failures == 1


async def test_a_blocked_row_has_nothing_to_judge(
    narrative_judge: Callable[..., DeepEvalJudge],
) -> None:
    judge = narrative_judge(lambda name: StubMetric(1.0))
    row = load(AgentName.NARRATIVE)[0]
    empty = AgentOutput(row_id=row.row_id, producer="t", items=[])
    spec = judge.specs(AgentName.NARRATIVE)[0]
    assert await spec.fn(row, empty, EvalContext()) is None  # type: ignore[misc]
    assert judge.failures == 0


def test_only_the_prose_agents_get_judge_metrics(
    narrative_judge: Callable[..., DeepEvalJudge],
) -> None:
    judge = narrative_judge(lambda name: StubMetric(1.0))
    judged = {a for a in AgentName if judge.specs(a)}
    assert judged == {AgentName.NARRATIVE, AgentName.EXAMINER, AgentName.CRITIC}


def test_the_deepeval_metrics_carry_the_briefs_thresholds(
    judge_llm: Callable[..., Any],
) -> None:
    llm, _client, _meter = judge_llm()
    build = deepeval_factory(llm)
    assert build(FAITHFULNESS).threshold == 0.90  # type: ignore[attr-defined]
    assert build(ANSWER_RELEVANCY).threshold == 0.80  # type: ignore[attr-defined]
    assert build(HALLUCINATION_FREE).threshold == 0.90  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "agent", [AgentName.NARRATIVE, AgentName.EXAMINER, AgentName.CRITIC]
)
async def test_the_judge_reads_what_the_agent_was_shown_not_the_dataset_row(
    agent: AgentName, narrative_judge: Callable[..., DeepEvalJudge]
) -> None:
    row, output = _echo_output(agent)
    judge = narrative_judge(lambda name: StubMetric(1.0))
    pack = await judge._packs.build(row)  # noqa: SLF001
    case = judge_case(agent, row, output, pack)
    assert case is not None
    assert case.actual_output.strip()
    assert case.retrieval_context == context_lines(pack)
    assert not any(line in ("(none)", "") for line in case.retrieval_context)
    assert not any(
        line.startswith("Case FCRM") and "submission v1" in line
        for line in case.retrieval_context
    )
    if agent is AgentName.EXAMINER:
        assert case.input == row.input["question"]
