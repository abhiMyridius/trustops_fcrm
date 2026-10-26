"""Each model's own token limits, enforced per request (ADR-83).

Every tier lets one attempt spend 64,000 tokens: its ``budget.tokens`` plus its
``max_tokens`` output cap (``ai/models/tiers.yaml``). A model of a tier's chain
may take less than the tier asks for: a smaller output cap per request (Nova
Pro and Nova Lite stop at 10,000) or a context window a request can outgrow.
``tiers.yaml models.<id>.max_output_tokens`` and ``context_window`` record a
model's own limits where they are verified; a model without them is sent the
tier's figures unchanged (the 64k default).

:class:`ModelLimitsModel` wraps every Bedrock model of a chain
(:func:`fcrm_agents.models.bedrock_chain`) and, for each request:

- lowers ``max_tokens`` to the model's ``max_output_tokens``;
- lowers it again so the estimated input plus ``max_tokens`` fits the model's
  ``context_window``, and raises :class:`ModelLimitError` when the input alone
  does not fit, so the ``FallbackModel`` moves on to the next model of the chain;
- when Bedrock still rejects the request for asking too many output tokens (a
  400 ``ValidationException``), re-sends it once with ``max_tokens`` lowered to
  the limit the error names, or to :data:`SAFE_OUTPUT_TOKENS` when it names
  none, and keeps that cap for the rest of the attempt. A rejection for too
  long an input, or a second rejection, is raised as :class:`ModelLimitError`,
  and the chain falls through to its next model as it does for any API error.

Every lowering is logged. No log key contains "token": the log scrubber drops
any such key.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from math import ceil
from typing import Any, Final, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from fcrm_agents.context_pack import CHARS_PER_TOKEN_ESTIMATE

logger = logging.getLogger(__name__)

SAFE_OUTPUT_TOKENS: Final = 4096
"""The ``max_tokens`` a request is re-sent with when the model rejected a larger
one without saying its limit: every model in ``tiers.yaml`` takes it."""

_TOKEN_WORDS: Final = re.compile(r"token", re.I)
_OVER_WORDS: Final = re.compile(
    r"exceed|too (long|many|large)|greater than|maximum|limit|>|less (than )?or equal",
    re.I,
)
_INPUT_WORDS: Final = re.compile(r"input|prompt|context (length|window)|too long", re.I)
_NUMBER: Final = re.compile(r"\d[\d,]*")


class ModelLimits(BaseModel):
    """One model's own limits (``tiers.yaml models.<id>``); ``None`` is unknown,
    and an unknown limit is never enforced before the model answers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_tokens: Optional[int] = Field(default=None, gt=0)
    context_window: Optional[int] = Field(default=None, gt=0)


class ModelLimitError(ModelAPIError):
    """A request does not fit the model it was meant for. It is a
    :class:`~pydantic_ai.exceptions.ModelAPIError`, so a ``FallbackModel`` tries
    the next model of its chain."""


def limits_from_config(entry: Mapping[str, Any]) -> ModelLimits:
    """The :class:`ModelLimits` of one ``tiers.yaml models.<id>`` entry."""
    return ModelLimits(
        max_output_tokens=entry.get("max_output_tokens"),
        context_window=entry.get("context_window"),
    )


def estimate_input_tokens(
    messages: list[ModelMessage], params: ModelRequestParameters
) -> int:
    """The input of a request at ``chars / 3.6`` over the serialized messages
    and the tool definitions. The JSON framing makes it err high, which is the
    safe side for a context-window check."""
    chars = len(ModelMessagesTypeAdapter.dump_json(messages))
    chars += sum(
        len(json.dumps(tool.parameters_json_schema, separators=(",", ":")))
        + len(tool.name)
        + len(tool.description or "")
        for tool in [*params.function_tools, *params.output_tools]
    )
    return ceil(chars / CHARS_PER_TOKEN_ESTIMATE)


def _error_text(exc: ModelHTTPError) -> str:
    """The provider's own message of a rejected request, else the error text."""
    body = exc.body
    if isinstance(body, Mapping):
        error = body.get("Error")
        if isinstance(error, Mapping):
            return f"{error.get('Code', '')} {error.get('Message', '')}".strip()
        message = body.get("message") or body.get("Message")
        if isinstance(message, str):
            return message
    return str(exc)


def is_token_limit_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a 400 rejection for asking more tokens than the
    model takes (an output cap or a context length)."""
    if not isinstance(exc, ModelHTTPError) or exc.status_code != 400:
        return False
    text = _error_text(exc)
    return bool(_TOKEN_WORDS.search(text) and _OVER_WORDS.search(text))


def _is_input_error(text: str) -> bool:
    """Whether a token-limit rejection is about the input, which a smaller
    ``max_tokens`` cannot fix."""
    return bool(_INPUT_WORDS.search(text)) and not re.search(
        r"max_?tokens|maxtokens|output", text, re.I
    )


def limit_named_in(text: str, asked: int) -> Optional[int]:
    """The largest number in ``text`` below ``asked``: the limit a rejection of
    ``max_tokens=asked`` names ("max_tokens: 70000 > 64000"), or ``None``."""
    numbers = [int(raw.replace(",", "")) for raw in _NUMBER.findall(text)]
    below = [n for n in numbers if 0 < n < asked]
    return max(below) if below else None


class ModelLimitsModel(WrapperModel):
    """One model of a chain held to its own limits (module doc). One instance
    serves one attempt (the chain is built per attempt): a limit learned from a
    rejection holds for the attempt's later requests."""

    def __init__(self, wrapped: Model, limits: Optional[ModelLimits] = None) -> None:
        """Wrap ``wrapped``.

        Args:
            wrapped: One model of the chain (a ``BedrockConverseModel``).
            limits: Its verified limits; ``None`` or an empty one enforces
                nothing up front and only handles a rejection.
        """
        super().__init__(wrapped)
        self.limits = limits or ModelLimits()
        self.learned_output_cap: Optional[int] = None

    @property
    def output_cap(self) -> Optional[int]:
        """The lowest output cap known for this model: verified or learned."""
        caps = [
            c for c in (self.limits.max_output_tokens, self.learned_output_cap) if c
        ]
        return min(caps) if caps else None

    def sized(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        params: ModelRequestParameters,
    ) -> Optional[ModelSettings]:
        """``model_settings`` with ``max_tokens`` lowered to what this model
        takes (left as given when no limit applies).

        Raises:
            ModelLimitError: the estimated input alone fills the context window.
        """
        asked = (model_settings or {}).get("max_tokens")
        allowed = asked
        cap = self.output_cap
        if cap is not None:
            allowed = cap if allowed is None else min(allowed, cap)
        window = self.limits.context_window
        if window is not None:
            room = window - estimate_input_tokens(messages, params)
            if room < 1:
                msg = (
                    f"the request's estimated input does not fit the "
                    f"{window}-token context window"
                )
                raise ModelLimitError(self.model_name, msg)
            allowed = room if allowed is None else min(allowed, room)
        if allowed == asked:
            return model_settings
        logger.info(
            "output allowance lowered to the model's limit: %s to %s",
            asked,
            allowed,
            extra={"model": self.model_name, "asked": asked, "allowed": allowed},
        )
        settings = ModelSettings(**(model_settings or {}))
        if allowed is not None:
            settings["max_tokens"] = allowed
        return settings

    def _relearn(self, exc: ModelHTTPError, settings: Optional[ModelSettings]) -> None:
        """Learn the output cap to re-send with after ``exc`` rejected the
        request: the limit the error names, else :data:`SAFE_OUTPUT_TOKENS`.

        Raises:
            ModelLimitError: the rejection is about the input, or the request
                already asked for no more than :data:`SAFE_OUTPUT_TOKENS`.
        """
        text = _error_text(exc)
        asked = int((settings or {}).get("max_tokens") or 0)
        if _is_input_error(text) or 0 < asked <= SAFE_OUTPUT_TOKENS:
            raise ModelLimitError(self.model_name, text) from exc
        named = limit_named_in(text, asked) if asked else None
        cap = named or SAFE_OUTPUT_TOKENS
        self.learned_output_cap = cap
        logger.warning(
            "model rejected the output allowance; re-sending at its limit: %s",
            text[:300],
            extra={"model": self.model_name, "asked": asked, "allowed": cap},
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """The wrapped request within the model's limits, re-sent once at a
        lower ``max_tokens`` when the model rejects the one it was given."""
        settings = self.sized(messages, model_settings, model_request_parameters)
        try:
            return await self.wrapped.request(
                messages, settings, model_request_parameters
            )
        except ModelHTTPError as exc:
            if not is_token_limit_error(exc):
                raise
            self._relearn(exc, settings)
        # The learned cap is below what was asked, so this lowers max_tokens.
        retry = self.sized(messages, settings, model_request_parameters)
        try:
            return await self.wrapped.request(messages, retry, model_request_parameters)
        except ModelHTTPError as exc:
            if is_token_limit_error(exc):
                raise ModelLimitError(self.model_name, _error_text(exc)) from exc
            raise

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
        run_context: Optional[RunContext[Any]] = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """The wrapped streamed request within the model's limits (H2 does not
        stream, so a rejection is not re-sent here; it falls through the
        chain as a :class:`ModelLimitError`)."""
        settings = self.sized(messages, model_settings, model_request_parameters)
        try:
            async with self.wrapped.request_stream(
                messages, settings, model_request_parameters, run_context
            ) as stream:
                yield stream
        except ModelHTTPError as exc:
            if is_token_limit_error(exc):
                raise ModelLimitError(self.model_name, _error_text(exc)) from exc
            raise
