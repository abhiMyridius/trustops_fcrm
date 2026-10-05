"""Small helpers every core's step handlers share: reading the trigger's payload
as its contract model, and showing an event on a case's stream through S1.

The intake core carries private copies of both (its own lane's); the decision
and closing cores use these.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Optional

from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_internal import UiActor, UiEventIn, UiVisibility
from fcrm_contracts.events.payloads import HarnessStep
from pydantic import BaseModel, ValidationError

from fcrm_caseflow.steps import StepDeps

#: Who H1 is on a stream entry.
H1_ACTOR: Final = UiActor(id="H1", role="system", component="H1")


def payload_of[P: BaseModel](step: HarnessStep, model: type[P]) -> P:
    """The trigger's payload (``step.args["payload"]``) as ``model``.

    Raises:
        fcrm_common.errors.ValidationError: The payload is missing or does not
            match the contract (the step is retried, then parked). Only the
            number of errors is reported, never the payload.
    """
    try:
        return model.model_validate(step.args.get("payload"))
    except ValidationError as exc:
        raise FcrmValidationError(
            f"the {step.step.value} payload is not a valid {model.__name__}",
            step_id=step.step_id,
            errors_count=exc.error_count(),
        ) from exc


async def show(
    deps: StepDeps,
    step: HarnessStep,
    event_type: str,
    visibility: UiVisibility,
    summary: str,
    detail: Optional[Mapping[str, Any]] = None,
    *,
    record_seq: Optional[int] = None,
    ordinal: int = 0,
) -> None:
    """Ask S1 to show an event on the case stream, with an ``Idempotency-Key``
    derived from ``(step, type, ordinal)``.
    """
    event = UiEventIn(
        type=event_type,
        visibility=visibility,
        summary=summary,
        detail=dict(detail or {}),
        actor=H1_ACTOR,
        record_seq=record_seq,
    )
    await deps.require_case().post_event(
        step.case_id,
        event,
        idempotency_key=deterministic_id(
            "idem", step.step_id, "ui", event_type, str(ordinal)
        ),
    )
