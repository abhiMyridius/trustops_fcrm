"""The judge model: DeepEval's ``AmazonBedrockModel`` on the ``judge`` tier.

``ai/models/tiers.yaml`` gives the ``judge`` tier a different model from the
assessor it grades (ADR-04): the strong tier runs on a Claude model, the judge
on a model of another family, so an agent is never graded by itself. This module reads
the tier's primary model, its output cap and its price from that file; no
model id is written here.

:class:`BedrockJudgeLLM` *is* DeepEval's ``AmazonBedrockModel`` (so every
DeepEval metric accepts it) with three changes:

- **Construction.** Built from a :class:`JudgeSettings` read off ``tiers.yaml``,
  with ``generation_kwargs`` holding only ``maxTokens``. The judge model
  rejects sampling parameters (``tiers.yaml models.<id>.temperature: omit``),
  so none is ever sent; DeepEval's own default is to send none either.
- **Metering.** Every call is recorded in a :class:`~fcrm_evals.usage.UsageMeter`
  (tokens from the Converse response, latency, the model that answered), so a
  run can report ``judge_usage`` exactly.
- **No ``.env``.** ``DEEPEVAL_DISABLE_DOTENV`` is set before DeepEval is
  imported: DeepEval otherwise loads every ``.env`` file in the working
  directory into the process on import, and this repository never reads
  ``.env*`` (nor lets a dependency do it for the tests). Its telemetry is
  opted out for the same reason.

DeepEval and aiobotocore come with the ``live`` extra (``fcrm-evals[live]``).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, Optional

os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

try:
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
    from deepeval.errors import DeepEvalError
    from deepeval.models import AmazonBedrockModel
    from tenacity import RetryError
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    msg = "the LLM judge needs the live extra: install fcrm-evals[live] (deepeval)"
    raise ImportError(msg) from exc
from pydantic import BaseModel, ValidationError

from fcrm_evals.usage import TOKENS_PER_PRICE_UNIT, PriceTable, UsageMeter

logger = logging.getLogger(__name__)

JUDGE_TIER: Final[str] = "judge"
"""The ``tiers.yaml`` tier eval judges run on."""
REGION_VARIABLES: Final[tuple[str, ...]] = ("AWS_REGION", "AWS_DEFAULT_REGION")
CONNECT_TIMEOUT_S: Final[int] = 10
CLIENT_ATTEMPTS: Final[int] = 3
ClientFactory = Callable[[], AbstractAsyncContextManager[Any]]
JUDGE_ERRORS: Final = (
    DeepEvalError,
    BotoCoreError,
    ClientError,
    RetryError,
    ValidationError,
    TimeoutError,
    ValueError,
    RuntimeError,
)
"""What one judge call can raise: a transport error, a verdict that does not
parse or validate, a timeout, DeepEval's own retries giving up
(``RetryError``). The metric that made the call records the row as *not
measured*; anything else is a bug and propagates."""


class JudgeConfigError(Exception):
    """``tiers.yaml`` cannot supply a judge (no tier, no price, no region)."""


@dataclass(frozen=True)
class JudgeSettings:
    """What the judge model needs, read from ``tiers.yaml``."""

    model_id: str
    region: str
    max_tokens: int
    wall_clock_s: int
    input_usd_per_token: float
    output_usd_per_token: float

    @classmethod
    def from_tiers(
        cls, config: Mapping[str, Any], region: Optional[str] = None
    ) -> JudgeSettings:
        """The ``judge`` tier's primary model, cap and price.

        Raises:
            JudgeConfigError: no ``judge`` tier, no price row for its model,
                or no region to call Bedrock in.
        """
        entry = config.get("tiers", {}).get(JUDGE_TIER)
        if not isinstance(entry, Mapping):
            msg = f"tiers.yaml has no {JUDGE_TIER!r} tier"
            raise JudgeConfigError(msg)
        model_id = str(entry["primary"])
        price = PriceTable.from_tiers(config).prices.get(model_id)
        if price is None:
            msg = (
                f"tiers.yaml prices_usd_per_mtok has no row for the {JUDGE_TIER} model"
            )
            raise JudgeConfigError(msg)
        return cls(
            model_id=model_id,
            region=region or resolve_region(),
            max_tokens=int(entry["max_tokens"]),
            wall_clock_s=int(entry["wall_clock_s"]),
            input_usd_per_token=price.input / TOKENS_PER_PRICE_UNIT,
            output_usd_per_token=price.output / TOKENS_PER_PRICE_UNIT,
        )


def resolve_region() -> str:
    """The AWS region: ``AWS_REGION``, ``AWS_DEFAULT_REGION``, then the AWS
    config file's.

    Raises:
        JudgeConfigError: none of them is set.
    """
    for name in REGION_VARIABLES:
        if os.environ.get(name):
            return os.environ[name]
    import botocore.session

    region = botocore.session.get_session().get_config_variable("region")
    if not region:
        msg = "no AWS region: set AWS_REGION or configure one for the AWS CLI"
        raise JudgeConfigError(msg)
    return str(region)


class BedrockJudgeLLM(AmazonBedrockModel):  # type: ignore[no-untyped-call]
    """``AmazonBedrockModel`` on the judge tier, metered (module docstring).

    ``client_factory`` replaces the aiobotocore client with a fake in unit
    tests; production leaves it ``None``.
    """

    def __init__(
        self,
        settings: JudgeSettings,
        meter: UsageMeter,
        *,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        """Build the model for ``settings``; calls are recorded on ``meter``."""
        # DeepEval cancels a call after 88.5 s unless told otherwise, and
        # botocore gives up reading after 60 s; the judge tier's budget is its
        # wall clock (``tiers.yaml``), so both follow it. The client config
        # replaces DeepEval's own (retries: a few, adaptive, never unbounded).
        os.environ.setdefault(
            "DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", str(settings.wall_clock_s)
        )
        super().__init__(
            model=settings.model_id,
            region=settings.region,
            generation_kwargs={"maxTokens": settings.max_tokens},
            cost_per_input_token=settings.input_usd_per_token,
            cost_per_output_token=settings.output_usd_per_token,
            config=Config(
                read_timeout=settings.wall_clock_s,
                connect_timeout=CONNECT_TIMEOUT_S,
                retries={"max_attempts": CLIENT_ATTEMPTS, "mode": "adaptive"},
            ),
        )
        self._settings = settings
        self._meter = meter
        self._client_factory = client_factory

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[Any]:
        if self._client_factory is None:
            async with super()._get_client() as client:
                yield client
        else:
            async with self._client_factory() as client:
                yield client

    async def a_generate(  # type: ignore[override]
        self, prompt: str, schema: Optional[type[BaseModel]] = None
    ) -> tuple[Any, Any]:
        """Generate and meter one call (DeepEval's ``(output, cost)`` pair)."""
        started = time.perf_counter()
        output, cost = await super().a_generate(prompt, schema)
        self._meter.record(
            self._settings.model_id,
            input_tokens=int(getattr(cost, "input_tokens", 0) or 0),
            output_tokens=int(getattr(cost, "output_tokens", 0) or 0),
            latency_s=time.perf_counter() - started,
        )
        return output, cost
