"""The live runtime against malformed and hostile answers, for all nine agents.

Every job goes through the real :func:`fcrm_agent_worker.live_runtime.
run_agent_live` (pre-guards, the attempt loop, the per-request allowance, the
in-run repair, Guard 4 output shape and Guard 5 rules) with a ``FunctionModel``
behind the ``model_factory`` seam: no Bedrock, no network (ADR-59).

Each agent's base case is an eval reference row's pack and items that pass as
they stand (``base_cases`` in this folder's ``conftest.py``). A **mutation**
rewrites how those items reach the output tool (a list sent as JSON text with
raw line breaks, a trailing comma, a Markdown fence, an empty list, an extra
key, 200 copies...); a **scenario** scripts attempt by attempt (a refused source
then a good answer, a fallback-chain failure, a timeout, a tool round trip).
:data:`EXPECTED` is what the runtime does with each, per agent where agents
differ: the verdict class, the attempts used and whether the accepted quotes
are byte-for-byte the ones meant (``None`` where that is not a question: no
quotes kept, items dropped, or the agent's items carry no quote).

The fast subset (the scenarios and seven key mutations) runs in ``make check``;
the full mutation matrix and the 50-job concurrency check (sequential, gathered
and gathered in reverse must agree job for job) are ``slow`` (``make
test-slow``).
"""

from __future__ import annotations

import asyncio
import copy
import json
import zlib
from collections import Counter
from collections.abc import Callable
from typing import Any, Optional

import pytest
from fcrm_agent_worker.live_runtime import RETRY_HEADER, run_agent_live
from fcrm_agents.runtime import RunVerdict
from fcrm_agents.spec import REGISTRY
from fcrm_contracts.enums import AgentName
from fcrm_guardrails import CaseBudget
from pydantic_ai.exceptions import FallbackExceptionGroup, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

AGENTS = list(AgentName)

# --- quote helpers ------------------------------------------------------------

# Placeholders for raw characters, resolved only when the JSON text is built,
# so ``json.dumps`` cannot escape them.
NL, TAB, DQ, NUL, BS = "", "", "", "", ""
RAW = {NL: "\n", TAB: "\t", DQ: '"', NUL: "\x00", BS: "\\"}

QUOTE_KEYS = ("quote",)
FALLBACK_KEYS = ("text",)
"""For an agent whose items carry no quote at all (the examiner)."""

Items = list[dict[str, Any]]
QuoteFn = Callable[[str], str]


def resolve(text: str) -> str:
    """``text`` with every placeholder replaced by its raw character."""
    for placeholder, raw in RAW.items():
        text = text.replace(placeholder, raw)
    return text


def _quotes(obj: Any, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys and isinstance(value, str):
                out.append(value)
            elif key in keys and isinstance(value, list):
                out += [x for x in value if isinstance(x, str)]
            else:
                out += _quotes(value, keys)
    elif isinstance(obj, list):
        for value in obj:
            out += _quotes(value, keys)
    return out


def _keys_for(obj: Any) -> tuple[str, ...]:
    return QUOTE_KEYS if _quotes(obj, (*QUOTE_KEYS, "quotes")) else FALLBACK_KEYS


def _map(obj: Any, fn: QuoteFn, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in keys and isinstance(value, str):
                out[key] = fn(value)
            elif key == "quotes" and keys == QUOTE_KEYS and isinstance(value, list):
                out[key] = [fn(x) if isinstance(x, str) else x for x in value]
            else:
                out[key] = _map(value, fn, keys)
        return out
    if isinstance(obj, list):
        return [_map(value, fn, keys) for value in obj]
    return obj


def map_quotes(obj: Any, fn: QuoteFn) -> Any:
    """``obj`` with ``fn`` applied to each verbatim-bearing string."""
    return _map(obj, fn, _keys_for(obj))


def quotes_of(obj: Any, ref: Any = None) -> list[str]:
    """The verbatim-bearing strings of ``obj`` (``quote``/``quotes``, or
    ``text`` when there are none); ``ref`` picks the key set, so a mutated copy
    is read with its base's keys."""
    keys = _keys_for(obj if ref is None else ref)
    return _quotes(obj, keys if keys == FALLBACK_KEYS else (*QUOTE_KEYS, "quotes"))


def ctrl_in_quote(quote: str) -> str:
    """A raw newline and tab where the quote had spaces (Guard 4 collapses
    whitespace, so it still matches; byte-exactness is the question)."""
    return quote.replace(" ", NL, 1).replace(" ", TAB, 1)


def dq_in_quote(quote: str) -> str:
    """The quote wrapped in unescaped double quotation marks, with a raw
    newline inside."""
    return DQ + ctrl_in_quote(quote) + DQ


def bad_escape(quote: str) -> str:
    """An invalid JSON escape (backslash-apostrophe) where the quote has a
    space, plus a raw newline: what a model that "escapes" an apostrophe
    writes. The apostrophe replaces a space, so the quote is no longer
    verbatim even once the escape is mended."""
    return quote.replace(" ", NL, 1).replace(" ", BS + "'", 1)


def smart(quote: str) -> str:
    """Typographic quotation marks around the quote and inside it."""
    return "“" + quote.replace("'", "’") + "”"


def nul(quote: str) -> str:
    """A NUL character where the quote had its first space."""
    return quote.replace(" ", NUL, 1)


def dumps(obj: Any) -> str:
    """Compact JSON text, non-ASCII kept."""
    return json.dumps(obj, ensure_ascii=False)


def logical(items: Items, fn: Optional[QuoteFn]) -> Items:
    """The items as meant: ``fn`` applied to each quote, placeholders
    resolved to raw characters."""
    if fn is None:
        return copy.deepcopy(items)
    keys = _keys_for(items)
    resolved: Items = _map(_map(items, fn, keys), resolve, keys)
    return resolved


def raw_json_text(items: Items, fn: QuoteFn) -> str:
    """The items as JSON text whose quotes carry *unescaped* raw characters
    (a model writing the list as a string literally)."""
    return resolve(dumps(map_quotes(items, fn)))


def junk(items: Items, extra: dict[str, Any]) -> Items:
    """Each item with ``extra`` keys added."""
    return [{**item, **extra} for item in items]


def big(items: Items, n: int = 200) -> Items:
    """``n`` items, the base items repeated."""
    return [copy.deepcopy(items[i % len(items)]) for i in range(n)]


# --- mutations ----------------------------------------------------------------

Mutation = Callable[[Items, str], tuple[Any, Optional[list[str]]]]
"""``(items, output key) -> (output-tool args, the quotes meant)``."""


def mutation(
    args: Callable[[Items, str], Any],
    fn: Optional[QuoteFn] = None,
    sent: Callable[[Items], Items] = lambda it: it,
) -> Mutation:
    """A mutation sending ``args``; the quotes meant are ``sent``'s items with
    ``fn`` applied to each quote."""

    def build(items: Items, key: str) -> tuple[Any, Optional[list[str]]]:
        return args(items, key), quotes_of(logical(sent(items), fn), items)

    return build


def _cut(text: str, of: str) -> str:
    """``text`` cut at 60 % of the length of ``of``."""
    return text[: int(len(of) * 0.6)]


MUTATIONS: dict[str, Mutation] = {
    "proper_list": mutation(lambda it, k: {k: it}),
    "json_str_escaped": mutation(lambda it, k: {k: dumps(it)}),
    "json_str_raw_nl_tab": mutation(
        lambda it, k: {k: raw_json_text(it, ctrl_in_quote)}, ctrl_in_quote
    ),
    "json_str_raw_nl_unesc_dq": mutation(
        lambda it, k: {k: raw_json_text(it, dq_in_quote)}, dq_in_quote
    ),
    "json_str_raw_nl_bad_escape": mutation(
        lambda it, k: {k: raw_json_text(it, bad_escape)}, bad_escape
    ),
    "json_str_raw_nl_trailing_comma": mutation(
        lambda it, k: {k: raw_json_text(it, ctrl_in_quote)[:-1] + ",]"}, ctrl_in_quote
    ),
    "json_str_escaped_apostrophe": mutation(
        lambda it, k: {k: resolve(dumps(it)).replace("'", BS + "'")}
    ),
    "json_str_raw_nl_truncated": mutation(
        lambda it, k: {k: _cut(raw_json_text(it, ctrl_in_quote), dumps(it))}
    ),
    "list_nl_tab_in_quote": mutation(
        lambda it, k: {k: logical(it, ctrl_in_quote)}, ctrl_in_quote
    ),
    "list_dq_in_quote": mutation(
        lambda it, k: {k: logical(it, dq_in_quote)}, dq_in_quote
    ),
    "trailing_comma": mutation(lambda it, k: {k: dumps(it)[:-1] + ",]"}),
    "trailing_comma_obj": mutation(lambda it, k: {k: dumps(it)[:-2] + ",}]"}),
    "md_fenced_json": mutation(
        lambda it, k: {
            k: "```json\n" + json.dumps(it, indent=2, ensure_ascii=False) + "\n```"
        }
    ),
    "truncated_mid_item": mutation(lambda it, k: {k: _cut(dumps(it), dumps(it))}),
    "empty_args": mutation(lambda it, k: {}),
    "empty_list": mutation(lambda it, k: {k: []}, sent=lambda it: []),
    "single_object": mutation(lambda it, k: {k: it[0]}, sent=lambda it: it[:1]),
    "wrong_key": mutation(lambda it, k: {"items": it}),
    "items_as_json_strs": mutation(lambda it, k: {k: [dumps(i) for i in it]}),
    "junk_empty_keys": mutation(
        lambda it, k: {k: junk(it, {"source_ref_note": None, "#": "", "x_list": []})}
    ),
    "junk_nonempty_keys": mutation(
        lambda it, k: {k: junk(it, {"source_ref_note": "see clause above"})}
    ),
    "double_encoded": mutation(lambda it, k: {k: dumps(dumps(it))}),
    "smart_quotes_in_quote": mutation(lambda it, k: {k: logical(it, smart)}, smart),
    "nul_in_quote": mutation(lambda it, k: {k: logical(it, nul)}, nul),
    "nul_in_json_str": mutation(lambda it, k: {k: raw_json_text(it, nul)}, nul),
    "large_200": mutation(lambda it, k: {k: big(it)}, sent=big),
    "args_str_valid": mutation(lambda it, k: dumps({k: it})),
    "args_str_raw_nl": mutation(
        lambda it, k: resolve(dumps({k: map_quotes(it, ctrl_in_quote)})), ctrl_in_quote
    ),
    "args_str_truncated": mutation(lambda it, k: _cut(dumps({k: it}), dumps({k: it}))),
}


# --- scenarios ----------------------------------------------------------------


def fallback_group() -> FallbackExceptionGroup:
    """What a ``FallbackModel`` raises when every model of the chain failed."""
    return FallbackExceptionGroup(
        "All models from FallbackModel failed",
        [ModelHTTPError(status_code=503, model_name="fake", body="ServiceUnavailable")],
    )


def read_timeout() -> TimeoutError:
    """A transport read timeout."""
    return TimeoutError("read timed out")


BAD_SOURCE = "@bad_source"
TOOL_THEN_OK = "@tool_then_ok"

#: Attempt by attempt: a mutation name, :data:`BAD_SOURCE`, :data:`TOOL_THEN_OK`
#: or an exception factory. The last entry repeats.
SCENARIOS: dict[str, list[Any]] = {
    "A1_unesc_dq->A2_ok": ["json_str_raw_nl_unesc_dq", "proper_list"],
    "A1_trailing_comma->A2_ok": ["trailing_comma", "proper_list"],
    "A1_bad_source->A2_ok": [BAD_SOURCE, "proper_list"],
    "A1_fallback_group->A2_ok": [fallback_group, "proper_list"],
    "A1_timeout->A2_ok": [read_timeout, "proper_list"],
    "A1_fallback_group->A2_fallback_group": [fallback_group, fallback_group],
    "A1_unesc_dq->A2_unesc_dq": [
        "json_str_raw_nl_unesc_dq",
        "json_str_raw_nl_unesc_dq",
    ],
    "A1_tool_call(research)->ok": [TOOL_THEN_OK],
}
SEARCH_ARGS = {
    "query": "cash pickup agent network",
    "top_k": 3,
    "filters": {"corpus_version": 14},
}

PLANS: dict[str, list[Any]] = {
    **{name: [name] for name in MUTATIONS},
    **SCENARIOS,
}

FAST_MUTATIONS = (
    "proper_list",
    "json_str_raw_nl_unesc_dq",
    "trailing_comma",
    "empty_list",
    "junk_empty_keys",
    "wrong_key",
    "large_200",
)


def bad_source(items: Items) -> Items:
    """The items citing a case no one gave the agent."""
    bad = {"kind": "case", "case_id": "FCRM-2026-9999"}
    out = []
    for original in items:
        item = copy.deepcopy(original)
        if "source_ref" in item:
            item["source_ref"] = bad
        for key in ("reasons", "sentences", "fields"):
            if isinstance(item.get(key), list):
                item[key] = [
                    {**r, "source_ref": bad}
                    if isinstance(r, dict) and "source_ref" in r
                    else r
                    for r in item[key]
                ]
        out.append(item)
    return out


def output_key(info: AgentInfo) -> str:
    """The output tool's one argument (``response``)."""
    properties = info.output_tools[0].parameters_json_schema.get("properties", {})
    return next(iter(properties)) if len(properties) == 1 else "response"


def user_text(messages: list[ModelMessage]) -> str:
    """Every user prompt part of ``messages``."""
    return "\n".join(
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


# --- one job ------------------------------------------------------------------


async def run_job(
    env: Any, case: Any, plan: list[Any], job_id: str, *, jitter: bool = False
) -> dict[str, Any]:
    """One job through ``run_agent_live``; ``plan[n]`` drives attempt n + 1
    (every request of it, the in-run repair included). Returns what the job
    did, every field deterministic."""
    seen: list[tuple[int, str]] = []
    state = {"attempt": 0}
    expected_quotes: dict[int, Optional[list[str]]] = {}

    def factory(models: Any, slot_plan: Any) -> FunctionModel:
        state["attempt"] += 1
        attempt = state["attempt"]
        behaviour = plan[min(attempt, len(plan)) - 1]

        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            if jitter:
                # Per job and attempt, so gathered jobs interleave differently.
                await asyncio.sleep(
                    (zlib.crc32(f"{job_id}:{attempt}".encode()) % 7) / 1000
                )
            seen.append((attempt, user_text(messages)))
            if callable(behaviour):
                raise behaviour()
            key = output_key(info)
            if (
                behaviour == TOOL_THEN_OK
                and case.agent is AgentName.RESEARCH
                and not any(isinstance(m, ModelResponse) for m in messages)
            ):
                search = ToolCallPart(tool_name="policy_search", args=SEARCH_ARGS)
                return ModelResponse(parts=[search])
            name = "proper_list" if behaviour == TOOL_THEN_OK else behaviour
            args: Any
            if name == BAD_SOURCE:
                args = {key: bad_source(case.items)}
                expected_quotes[attempt] = None
            else:
                args, expected_quotes[attempt] = MUTATIONS[name](case.items, key)
            tool = info.output_tools[0].name
            return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args)])

        return FunctionModel(respond, model_name=f"fake-a{attempt}")

    result, model_used, usage = await run_agent_live(
        REGISTRY[case.agent],
        case.pack,
        job_id,
        registry=env.registry,
        permit=env.permit,
        budget=CaseBudget(case.pack.case_id),
        instructions=None,
        tiers_config=env.tiers,
        model_factory=factory,
    )
    attempts = len(result.attempts)
    accepted = [item.model_dump(mode="json") for item in result.accepted]
    return {
        "verdict": verdict_class(result, attempts),
        "attempts": attempts,
        "byte_exact": byte_exact(
            result, quotes_of(accepted, case.items), expected_quotes.get(attempts)
        ),
        "a2_feedback": retry_feedback([text for a, text in seen if a == 2]),
        "reason": result.reason,
        "accepted": accepted,
        "dropped": list(result.dropped),
        "model_used": model_used,
        "usage": dict(usage),
        "tokens_used": result.tokens_used,
        "tools_called": [list(a.tools_called) for a in result.attempts],
        "attempt_trail": [
            [(g.guard.value, g.status.value, g.summary) for g in a.guard_results]
            for a in result.attempts
        ],
    }


def retry_feedback(retry_texts: list[str]) -> Optional[str]:
    """What attempt 2 was shown after :data:`RETRY_HEADER` (``""``: no retry
    text; ``None``: no attempt 2)."""
    if not retry_texts:
        return None
    if RETRY_HEADER not in retry_texts[0]:
        return ""
    return retry_texts[0].split(RETRY_HEADER, 1)[1].strip()


def byte_exact(
    result: Any, got: list[str], want: Optional[list[str]]
) -> Optional[bool]:
    """Whether the accepted quotes are exactly the ones meant (all of them, or,
    when items were dropped, each kept one); ``None`` when the job did not
    pass or nothing was meant to be quoted."""
    if result.verdict not in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS):
        return None
    if want is None:
        return None
    if not result.dropped:
        return Counter(got) == Counter(want) if want else None
    return all(q in want for q in got) if got else None


def verdict_class(result: Any, attempts: int) -> str:
    """``pass``, ``pass_drops``, ``retry->pass``, ``retry->pass_drops`` or
    ``blocked@<guard>``."""
    if result.verdict is RunVerdict.BLOCKED:
        return f"blocked@{result.stopped_at.value}"
    drops = "pass_drops" if result.verdict is RunVerdict.PASS_WITH_DROPS else "pass"
    return f"retry->{drops}" if attempts > 1 else drops


# --- what the runtime does with each --------------------------------------------

Outcome = tuple[str, int, Optional[bool]]
"""``(verdict class, attempts, byte_exact)``."""

PASS: Outcome = ("pass", 1, True)
NOT_A_CALL: Outcome = ("blocked@output_shape", 2, None)
"""Not a valid output-tool call on either attempt (each repaired once)."""
UNSOURCED: Outcome = ("retry->pass_drops", 2, None)
"""Quotes no longer verbatim: refused, then dropped on the retry."""
EMPTY_BLOCKED: Outcome = ("blocked@output_shape", 2, None)
"""An agent that must answer is told its empty answer was empty, once."""
EMPTY_PASS: Outcome = ("pass", 1, None)
NO_QUOTE = AgentName.EXAMINER
"""The examiner's items carry no quote (its ``text`` is not checked verbatim)."""
MAY_BE_EMPTY = (AgentName.CRITIC, AgentName.WATCHLIST, AgentName.POLICY_GAP)

EXPECTED: dict[str, Outcome] = {
    "proper_list": PASS,
    "json_str_escaped": PASS,
    "json_str_raw_nl_tab": PASS,
    "json_str_raw_nl_unesc_dq": PASS,
    "json_str_raw_nl_bad_escape": UNSOURCED,
    "json_str_raw_nl_trailing_comma": PASS,
    "json_str_escaped_apostrophe": PASS,
    "json_str_raw_nl_truncated": NOT_A_CALL,
    "list_nl_tab_in_quote": PASS,
    "list_dq_in_quote": PASS,
    "trailing_comma": PASS,
    "trailing_comma_obj": PASS,
    "md_fenced_json": PASS,
    "truncated_mid_item": NOT_A_CALL,
    "empty_args": NOT_A_CALL,
    "empty_list": EMPTY_BLOCKED,
    "single_object": PASS,
    "wrong_key": NOT_A_CALL,
    "items_as_json_strs": PASS,
    "junk_empty_keys": PASS,
    "junk_nonempty_keys": NOT_A_CALL,
    "double_encoded": PASS,
    "smart_quotes_in_quote": PASS,
    "nul_in_quote": UNSOURCED,
    "nul_in_json_str": UNSOURCED,
    "large_200": PASS,
    "args_str_valid": PASS,
    "args_str_raw_nl": NOT_A_CALL,
    "args_str_truncated": NOT_A_CALL,
    "A1_unesc_dq->A2_ok": PASS,
    "A1_trailing_comma->A2_ok": PASS,
    "A1_bad_source->A2_ok": ("retry->pass", 2, True),
    "A1_fallback_group->A2_ok": ("retry->pass", 2, True),
    "A1_timeout->A2_ok": ("retry->pass", 2, True),
    "A1_fallback_group->A2_fallback_group": NOT_A_CALL,
    "A1_unesc_dq->A2_unesc_dq": PASS,
    "A1_tool_call(research)->ok": PASS,
}

OVERRIDES: dict[tuple[str, AgentName], Outcome] = {
    # The assessment's dropped item leaves one kept item whose quote is exact.
    ("json_str_raw_nl_bad_escape", AgentName.ASSESSMENT): (
        "retry->pass_drops",
        2,
        True,
    ),
    # No quote to break: the mended text passes, without the backslash the
    # mutation wrote (the "quote meant" kept it), so it is not byte-exact.
    ("json_str_raw_nl_bad_escape", NO_QUOTE): ("pass", 1, False),
    ("nul_in_quote", NO_QUOTE): PASS,
    ("nul_in_json_str", NO_QUOTE): PASS,
    # No source to break.
    ("A1_bad_source->A2_ok", NO_QUOTE): ("pass", 1, None),
    # 200 copies are over the attempt budget plus one request's output: a budget
    # block, never retried.
    ("large_200", AgentName.EXAMINER): ("blocked@budget", 1, None),
    ("large_200", AgentName.POLICY_GAP): ("blocked@budget", 1, None),
    **{("empty_list", agent): EMPTY_PASS for agent in MAY_BE_EMPTY},
}


def expected(plan: str, agent: AgentName) -> Outcome:
    """What the runtime does with ``plan`` for ``agent``."""
    return OVERRIDES.get((plan, agent), EXPECTED[plan])


def test_the_tables_cover_every_plan() -> None:
    assert set(EXPECTED) == set(PLANS)
    assert {plan for plan, _agent in OVERRIDES} <= set(PLANS)
    assert set(FAST_MUTATIONS) <= set(MUTATIONS)


def _check(result: dict[str, Any], plan: str, agent: AgentName) -> None:
    outcome = (result["verdict"], result["attempts"], result["byte_exact"])
    assert outcome == expected(plan, agent), result["reason"]
    usage = result["usage"]
    assert result["tokens_used"] == usage["input_tokens"] + usage["output_tokens"]
    assert len(result["attempt_trail"]) == result["attempts"]
    if result["verdict"].startswith("blocked"):
        assert result["accepted"] == []
        assert result["reason"]


async def _run_cell(
    env: Any, cases: Any, plan: str, agent: AgentName
) -> dict[str, Any]:
    return await run_job(env, cases[agent], PLANS[plan], f"cell-{plan}-{agent.value}")


@pytest.mark.parametrize("agent", AGENTS, ids=[a.value for a in AGENTS])
@pytest.mark.parametrize("mutation", FAST_MUTATIONS)
async def test_key_mutations(
    mutation: str, agent: AgentName, adversarial_env: Any, base_cases: Any
) -> None:
    result = await _run_cell(adversarial_env, base_cases, mutation, agent)
    _check(result, mutation, agent)


@pytest.mark.parametrize("agent", AGENTS, ids=[a.value for a in AGENTS])
@pytest.mark.parametrize("scenario", list(SCENARIOS))
async def test_scenarios(
    scenario: str, agent: AgentName, adversarial_env: Any, base_cases: Any
) -> None:
    result = await _run_cell(adversarial_env, base_cases, scenario, agent)
    _check(result, scenario, agent)
    if scenario in ("A1_fallback_group->A2_ok", "A1_timeout->A2_ok"):
        # A failed model call is not the model's mistake: no retry text.
        assert result["a2_feedback"] == ""
        assert result["model_used"] == "fake-a2"
    if scenario == "A1_bad_source->A2_ok" and agent is not NO_QUOTE:
        assert result["a2_feedback"], "the retry is shown why it was refused"
    if scenario == "A1_tool_call(research)->ok" and agent is AgentName.RESEARCH:
        assert result["tools_called"] == [["policy_search"]]
    elif scenario == "A1_tool_call(research)->ok":
        assert result["tools_called"] == [[]]


@pytest.mark.slow
@pytest.mark.parametrize("agent", AGENTS, ids=[a.value for a in AGENTS])
@pytest.mark.parametrize("mutation", list(MUTATIONS))
async def test_every_mutation(
    mutation: str, agent: AgentName, adversarial_env: Any, base_cases: Any
) -> None:
    result = await _run_cell(adversarial_env, base_cases, mutation, agent)
    _check(result, mutation, agent)


@pytest.mark.slow
async def test_concurrent_jobs_do_what_the_same_jobs_do_one_by_one(
    adversarial_env: Any, base_cases: Any
) -> None:
    names = list(PLANS)
    jobs = [
        (AGENTS[i % len(AGENTS)], names[(i * 7) % len(names)], f"cc-{i}")
        for i in range(50)
    ]

    async def one(agent: AgentName, plan: str, job_id: str, jitter: bool) -> Any:
        return await run_job(
            adversarial_env, base_cases[agent], PLANS[plan], job_id, jitter=jitter
        )

    sequential = [await one(a, p, j, False) for a, p, j in jobs]
    gathered = await asyncio.gather(*(one(a, p, j, True) for a, p, j in jobs))
    reverse = await asyncio.gather(*(one(a, p, j, True) for a, p, j in reversed(jobs)))
    mismatches = [
        (job_id, label)
        for (_a, _p, job_id), alone, together, backwards in zip(
            jobs, sequential, gathered, reversed(reverse), strict=True
        )
        for label, other in (("gather", together), ("reversed", backwards))
        if other != alone
    ]
    assert mismatches == []
    for (agent, plan, _job_id), alone in zip(jobs, sequential, strict=True):
        _check(alone, plan, agent)
