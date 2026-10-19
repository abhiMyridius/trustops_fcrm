"""The output allowance of each model request (ADR-80).

An attempt has a token budget (Guard 3's ``AttemptPlan.token_limit``: input
and output of every request of the attempt together), and every request has
an output cap (the tier's ``max_tokens``). Sending the tier's whole cap on
every request let a request that started near the end of the budget write an
answer the budget could not hold: Pydantic AI checks ``total_tokens_limit``
*after* the response arrives and before it reaches the message history, so a
valid final answer that tipped over was thrown away as a budget block.

:class:`OutputAllowanceModel` wraps the attempt's model and sizes
``max_tokens`` per request instead::

    allowance = max(floor, min(tier_max, attempt_limit - spent - estimated_input))

- ``spent`` is what the attempt's earlier requests used (measured input plus
  output, as the provider reported it);
- ``estimated_input`` is, for the first request, ``chars / 3.6`` over every
  message part, the instructions and the tool definitions' JSON schemas, plus
  :data:`REQUEST_OVERHEAD_TOKENS`; for a later request it is the previous
  request's *measured* input plus its output (which the model now reads back)
  plus the estimate of the messages added since;
- ``floor`` (:data:`OUTPUT_FLOOR_TOKENS`) keeps an answer possible even when
  the estimate says the budget is spent: the attempt may then overrun its
  budget, and :mod:`fcrm_agent_worker.live_runtime` keeps that answer and
  annotates the overrun rather than discarding it.

Every request's figures are kept as a :class:`RequestAllowance` (the
``AttemptRecord.allowances`` of the attempt) and logged, so the estimate can
be compared with what the provider measured. No log key contains "token":
the log scrubber drops any such key.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from math import ceil
from typing import Any, Final, Optional

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

logger = logging.getLogger(__name__)

OUTPUT_FLOOR_TOKENS: Final = 1024
"""The smallest output allowance a request is given (``AGENT_OUTPUT_FLOOR_TOKENS``
overrides it): below this an output-tool call of any agent is cut off."""

REQUEST_OVERHEAD_TOKENS: Final = 200
"""What a request costs beyond its text: role markers, the tool-use framing
and the provider's own system text."""

CHARS_PER_TOKEN: Final = 3.6
"""Characters per token of the estimate (``ai/guards/thresholds.yaml``'s
``len/3.6``); dense policy text and JSON run close to it."""


def tokens_for_chars(chars: int) -> int:
    """The estimated tokens of ``chars`` characters."""
    return ceil(chars / CHARS_PER_TOKEN)


def _part_text(part: Any) -> str:
    """The text one message part puts in front of the model."""
    if isinstance(part, ToolCallPart):
        return f"{part.tool_name} {part.args_as_json_str()}"
    if isinstance(part, ToolReturnPart):
        return f"{part.tool_name} {part.model_response_str()}"
    if isinstance(part, RetryPromptPart):
        return part.model_response()
    content = getattr(part, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return " ".join(item for item in content if isinstance(item, str))
    return str(content)


def _messages_chars(messages: Sequence[ModelMessage]) -> int:
    """Characters of every part of ``messages``."""
    return sum(
        len(_part_text(part))
        for message in messages
        if isinstance(message, (ModelRequest, ModelResponse))
        for part in message.parts
    )


def schema_chars(schemas: Sequence[Mapping[str, Any]]) -> int:
    """Characters of JSON schemas as the model receives them."""
    return sum(len(json.dumps(schema, separators=(",", ":"))) for schema in schemas)


def _tool_schemas(params: ModelRequestParameters) -> list[dict[str, Any]]:
    """Each offered tool's name, description and parameters schema."""
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.parameters_json_schema,
        }
        for tool in [*params.function_tools, *params.output_tools]
    ]


def _instructions_chars(
    messages: Sequence[ModelMessage], params: ModelRequestParameters
) -> int:
    """Characters of the instructions the request carries (the request
    parameters' parts when present, else the last request message's)."""
    if params.instruction_parts:
        return sum(len(part.content) for part in params.instruction_parts)
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return len(message.instructions or "")
    return 0


def estimate_request_tokens(
    messages: Sequence[ModelMessage], params: ModelRequestParameters
) -> int:
    """The estimated input of a whole request: its messages, instructions and
    tool definitions at ``chars / 3.6``, plus :data:`REQUEST_OVERHEAD_TOKENS`."""
    chars = (
        _messages_chars(messages)
        + _instructions_chars(messages, params)
        + schema_chars(_tool_schemas(params))
    )
    return tokens_for_chars(chars) + REQUEST_OVERHEAD_TOKENS


def estimate_prompt_tokens(
    *texts: Optional[str], schemas: Sequence[Mapping[str, Any]] = ()
) -> int:
    """The estimated input of a first request built from ``texts``
    (instructions, user text) and tool ``schemas``, before it is built."""
    chars = sum(len(text or "") for text in texts) + schema_chars(schemas)
    return tokens_for_chars(chars) + REQUEST_OVERHEAD_TOKENS


def output_allowance(
    *, attempt_limit: int, spent: int, estimated_input: int, tier_max: int, floor: int
) -> int:
    """``max(floor, min(tier_max, attempt_limit - spent - estimated_input))``,
    the floor itself never above the tier's cap."""
    floor = min(floor, tier_max)
    return max(floor, min(tier_max, attempt_limit - spent - estimated_input))


@dataclass
class RequestAllowance:
    """One request's figures: the input estimated before it was sent, the
    output allowance (``max_tokens``) it was given and the input and output
    the provider measured (0 until the response arrives, and for a request
    that failed)."""

    estimated_input: int
    allowance: int
    actual_input: int = 0
    actual_output: int = 0

    def as_record(self) -> dict[str, int]:
        """The ``AttemptRecord.allowances`` entry."""
        return {
            "estimated_input": self.estimated_input,
            "allowance": self.allowance,
            "actual_input": self.actual_input,
            "actual_output": self.actual_output,
        }


class OutputAllowanceModel(WrapperModel):
    """A model whose every request gets the output allowance the attempt's
    budget leaves (module doc). One instance serves one attempt: it counts
    what that attempt's requests spent.
    """

    def __init__(
        self,
        wrapped: Model,
        *,
        attempt_limit: int,
        tier_max: int,
        floor: int = OUTPUT_FLOOR_TOKENS,
        log_extra: Optional[Mapping[str, Any]] = None,
        cut_off_note: Optional[str] = None,
    ) -> None:
        """Wrap ``wrapped`` for one attempt.

        Args:
            wrapped: The attempt's model (the tier's fallback chain).
            attempt_limit: The attempt's token budget (``AttemptPlan.token_limit``).
            tier_max: The tier's output cap per request (``max_tokens``).
            floor: The smallest allowance a request is given.
            log_extra: Context added to each request's log line (agent, job,
                attempt); its keys must not be ``LogRecord`` fields.
            cut_off_note: Added to the repair request that follows an answer
                cut off at its allowance: Pydantic AI's own retry prompt says
                only which fields are missing, and the model wrote the same
                length again.
        """
        super().__init__(wrapped)
        self.attempt_limit = attempt_limit
        self.tier_max = tier_max
        self.floor = floor
        self.log_extra = dict(log_extra or {})
        self.cut_off_note = cut_off_note
        self.allowances: list[RequestAllowance] = []
        self._history_len: Optional[int] = None

    @property
    def spent(self) -> int:
        """Input plus output the attempt's requests have used so far."""
        return sum(a.actual_input + a.actual_output for a in self.allowances)

    def estimate_input(
        self, messages: Sequence[ModelMessage], params: ModelRequestParameters
    ) -> int:
        """The next request's input: measured where a response has reported
        it (previous input + its output + the messages added since), else the
        character estimate of the whole request."""
        last = self.allowances[-1] if self.allowances else None
        if (
            last is not None
            and last.actual_input > 0
            and self._history_len is not None
            and len(messages) > self._history_len
        ):
            added = messages[self._history_len + 1 :]
            return (
                last.actual_input
                + last.actual_output
                + tokens_for_chars(_messages_chars(added))
            )
        return estimate_request_tokens(messages, params)

    def plan(
        self, messages: Sequence[ModelMessage], params: ModelRequestParameters
    ) -> RequestAllowance:
        """Size the next request and keep its figures."""
        estimated = self.estimate_input(messages, params)
        allowance = RequestAllowance(
            estimated_input=estimated,
            allowance=output_allowance(
                attempt_limit=self.attempt_limit,
                spent=self.spent,
                estimated_input=estimated,
                tier_max=self.tier_max,
                floor=self.floor,
            ),
        )
        self.allowances.append(allowance)
        self._history_len = len(messages)
        return allowance

    def _settled(self, allowance: RequestAllowance, usage: RequestUsage) -> None:
        """Keep the measured figures of a request and log them next to the
        estimate (the calibration ``CHARS_PER_TOKEN`` is checked against)."""
        allowance.actual_input = usage.input_tokens
        allowance.actual_output = usage.output_tokens
        logger.info(
            "model request sized: estimated input %d, allowance %d, measured "
            "input %d, output %d",
            allowance.estimated_input,
            allowance.allowance,
            allowance.actual_input,
            allowance.actual_output,
            extra={
                **self.log_extra,
                "estimated": allowance.estimated_input,
                "allowance": allowance.allowance,
                "measured_input": allowance.actual_input,
                "measured_output": allowance.actual_output,
            },
        )

    def _with_note(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        """``messages`` with :attr:`cut_off_note` added to the last request
        when it is the repair of an answer cut off at its allowance (the
        history itself is left as it is)."""
        if not self.cut_off_note or len(messages) < 2:
            return messages
        last, previous = messages[-1], messages[-2]
        if not (
            isinstance(last, ModelRequest)
            and isinstance(previous, ModelResponse)
            and previous.finish_reason == "length"
            and any(isinstance(part, RetryPromptPart) for part in last.parts)
        ):
            return messages
        logger.info(
            "repairing an answer cut off at its allowance, with a brevity note",
            extra=self.log_extra,
        )
        noted = replace(last, parts=[*last.parts, UserPromptPart(self.cut_off_note)])
        return [*messages[:-1], noted]

    @staticmethod
    def _with_allowance(
        model_settings: Optional[ModelSettings], allowance: RequestAllowance
    ) -> ModelSettings:
        settings = ModelSettings(**(model_settings or {}))
        settings["max_tokens"] = allowance.allowance
        return settings

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """The wrapped request with ``max_tokens`` set to the allowance."""
        messages = self._with_note(messages)
        allowance = self.plan(messages, model_request_parameters)
        response = await self.wrapped.request(
            messages,
            self._with_allowance(model_settings, allowance),
            model_request_parameters,
        )
        self._settled(allowance, response.usage)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
        run_context: Optional[RunContext[Any]] = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """The wrapped streamed request with ``max_tokens`` set to the
        allowance (H2 does not stream; kept so the wrapper is complete)."""
        messages = self._with_note(messages)
        allowance = self.plan(messages, model_request_parameters)
        async with self.wrapped.request_stream(
            messages,
            self._with_allowance(model_settings, allowance),
            model_request_parameters,
            run_context,
        ) as stream:
            yield stream
        self._settled(allowance, stream.usage)
