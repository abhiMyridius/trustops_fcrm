"""The S5 client for H1's decision core: what it does *after* people have voted
or an analyst has unlocked (``POST /outcomes``, ``POST /packs/{id}/supersede``,
``POST /packs/{id}/revote``) and the reads it needs to do it (the pack's drafted
conditions, the case's outcome).

Building and releasing a pack belong to the assessment core's own client
(:mod:`fcrm_caseflow.clients.decision_client`); this one never builds or
releases. Every write carries an ``Idempotency-Key`` derived from the step, so a
step that runs twice records one outcome and supersedes one pack.

**Refusals.** S5 refuses with a 409 whose problem ``type`` ends in its own
machine code (``not_tallied``, ``outcome_mismatch``, ``pack_decided``...).
:class:`fcrm_common.http.raise_for_problem` would flatten all of them into one
``ConflictError``, so this client reads the code itself and raises
:class:`DecisionRefusedError` carrying it. A refusal is *permanent*: the same
request fails the same way, so the step fails loudly (logged with the code, the
case and the pack, then dead-lettered) rather than being quietly retried into
success. What *is* retried is the transient kind (S5 unreachable, a timeout, a
5xx), which :class:`~fcrm_common.http.ServiceClient` raises as
``UpstreamUnavailableError`` / ``UpstreamTimeoutError``.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Final, Optional

import httpx
from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.http import ServiceClient, Timeouts, raise_for_problem
from fcrm_contracts.api.decision import (
    CaseOutcome,
    OutcomeRecord,
    PackView,
    RevoteRequest,
    RevoteResult,
    SupersedeRequest,
    SupersedeResult,
)
from pydantic import BaseModel

from fcrm_caseflow.clients import CALL_TIMEOUTS, parse_body, path_segment

logger = logging.getLogger(__name__)

DEPENDENCY: Final = "decision"

#: Route templates, relative to ``DECISION_SERVICE_URL`` (which already ends in
#: S5's mount prefix, ``/api/fcrm/decision``).
OUTCOMES_PATH: Final = "/outcomes"
SUPERSEDE_PATH: Final = "/packs/{pack_id}/supersede"
REVOTE_PATH: Final = "/packs/{pack_id}/revote"
PACK_PATH: Final = "/packs/{pack_id}"
LATEST_PACK_PATH: Final = "/cases/{case_id}/packs/latest"
CASE_OUTCOME_PATH: Final = "/cases/{case_id}/outcome"

#: Recording an outcome is the one call that decides a case, so it gets the
#: longest read deadline (30 s); the rest use the shared client deadline.
OUTCOME_TIMEOUTS: Final = Timeouts(connect=2.0, read=30.0, write=10.0, pool=5.0)


class DecisionRefusedError(ConflictError):
    """S5 refused a request with a machine code (``not_tallied``,
    ``outcome_mismatch``...). Permanent: repeating the request gets the same
    answer, so a step that meets one fails rather than retries into success.

    The code is :attr:`problem_code` (also in ``extra``); the HTTP status S5
    answered is ``extra["upstream_status"]``.
    """

    code: ClassVar[str] = "decision_refused"
    title: ClassVar[str] = "Decision service refused"

    @property
    def problem_code(self) -> str:
        """S5's machine code, the last segment of its problem ``type``."""
        return str(self.extra.get("problem_code", "unknown"))


def problem_code_of(response: httpx.Response) -> str:
    """The machine code in an error response's problem ``type`` (its last path
    segment), or ``"unknown"`` for a body that has none.
    """
    try:
        body = response.json()
    except ValueError:
        return "unknown"
    raw = body.get("type") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw or raw.startswith("about:"):
        return "unknown"  # RFC 9457's ``about:blank`` names no code
    return raw.rstrip("/").rsplit("/", 1)[-1] or "unknown"


def _detail_of(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"{DEPENDENCY} answered {response.status_code}."
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) else f"{DEPENDENCY} refused the request."


def _fill(template: str, **values: str) -> str:
    return template.format(**{k: path_segment(v) for k, v in values.items()})


def _json(model: BaseModel) -> Any:
    return model.model_dump(mode="json", exclude_none=True)


class DecisionVotesClient:
    """Calls S5 on behalf of H1's decision core.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``DECISION_SERVICE_URL``.
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def _send(
        self,
        method: str,
        path: str,
        *,
        route: str,
        json: Any = None,
        params: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout: Timeouts = CALL_TIMEOUTS,
    ) -> Any:
        """One request; the decoded JSON body, or the typed failure.

        Raises:
            DecisionRefusedError: S5 answered 409 with a machine code.
            fcrm_common.errors.NotFoundError: 404.
            fcrm_common.errors.ValidationError: 422.
            fcrm_common.errors.UpstreamUnavailableError: unreachable, a 5xx or
                an unexpected status (retried by the broker).
            fcrm_common.errors.UpstreamTimeoutError: S5 did not answer in time.
        """
        response = await self._client.request(
            method,
            path,
            json=json,
            params=params,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        if response.status_code == 409:
            code = problem_code_of(response)
            logger.error(
                "decision service refused a request",
                extra={
                    "dependency": DEPENDENCY,
                    "route": route,
                    "problem_code": code,
                },
            )
            raise DecisionRefusedError(
                _detail_of(response),
                dependency=DEPENDENCY,
                upstream_status=409,
                problem_code=code,
                route=route,
            )
        raise_for_problem(response, DEPENDENCY)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def record_outcome(
        self, record: OutcomeRecord, *, idempotency_key: str
    ) -> CaseOutcome:
        """``POST /outcomes``: record what the committee decided (committee
        mode: exactly the pack's tallied outcome) or an analyst's delegated
        close. Recording the same outcome again, with the same key or the same
        content, answers the stored one and announces nothing a second time.

        Raises:
            DecisionRefusedError: ``not_tallied``, ``outcome_mismatch``,
                ``outcome_already_recorded``, ``case_decided``,
                ``committee_pending``, ``idempotency_key_reused``...
        """
        body = await self._send(
            "POST",
            OUTCOMES_PATH,
            route=OUTCOMES_PATH,
            json=_json(record),
            idempotency_key=idempotency_key,
            timeout=OUTCOME_TIMEOUTS,
        )
        return parse_body(CaseOutcome, body, dependency=DEPENDENCY, route=OUTCOMES_PATH)

    async def confirm_revote(
        self, pack_id: str, *, idempotency_key: str
    ) -> RevoteResult:
        """``POST /packs/{pack_id}/revote``: confirm the round S5 opened when it
        tallied a 1-1-1 split. Writes nothing in S5.

        Raises:
            DecisionRefusedError: ``no_revote_pending``: the pack's last round
                did not end with no majority.
        """
        body = await self._send(
            "POST",
            _fill(REVOTE_PATH, pack_id=pack_id),
            route=REVOTE_PATH,
            json=_json(RevoteRequest()),
            idempotency_key=idempotency_key,
        )
        return parse_body(RevoteResult, body, dependency=DEPENDENCY, route=REVOTE_PATH)

    async def supersede(
        self, pack_id: str, reason: str, *, idempotency_key: str
    ) -> SupersedeResult:
        """``POST /packs/{pack_id}/supersede``: after the analyst unlocked, the
        pack takes no more votes, its cast votes are voided (kept) and
        re-requested for the next version. Idempotent in S5.

        Raises:
            DecisionRefusedError: ``pack_decided``: the pack already has an
                outcome.
        """
        body = await self._send(
            "POST",
            _fill(SUPERSEDE_PATH, pack_id=pack_id),
            route=SUPERSEDE_PATH,
            json=_json(SupersedeRequest(reason=reason)),
            idempotency_key=idempotency_key,
        )
        return parse_body(
            SupersedeResult, body, dependency=DEPENDENCY, route=SUPERSEDE_PATH
        )

    async def get_pack(self, pack_id: str) -> PackView:
        """``GET /packs/{pack_id}``: the frozen pack, with its drafted
        conditions.

        Raises:
            fcrm_common.errors.NotFoundError: no such pack.
        """
        body = await self._send(
            "GET", _fill(PACK_PATH, pack_id=pack_id), route=PACK_PATH
        )
        return parse_body(PackView, body, dependency=DEPENDENCY, route=PACK_PATH)

    async def latest_pack(self, case_id: str) -> PackView:
        """``GET /cases/{case_id}/packs/latest``: the case's newest pack.

        Raises:
            fcrm_common.errors.NotFoundError: the case has no pack.
        """
        body = await self._send(
            "GET",
            _fill(LATEST_PACK_PATH, case_id=case_id),
            route=LATEST_PACK_PATH,
        )
        return parse_body(PackView, body, dependency=DEPENDENCY, route=LATEST_PACK_PATH)

    async def case_outcome(self, case_id: str) -> Optional[CaseOutcome]:
        """``GET /cases/{case_id}/outcome``, or ``None`` while the case has no
        decisive outcome (S5 answers 404 until one exists).
        """
        try:
            body = await self._send(
                "GET",
                _fill(CASE_OUTCOME_PATH, case_id=case_id),
                route=CASE_OUTCOME_PATH,
            )
        except NotFoundError:
            return None
        return parse_body(
            CaseOutcome, body, dependency=DEPENDENCY, route=CASE_OUTCOME_PATH
        )


__all__ = [
    "DEPENDENCY",
    "OUTCOME_TIMEOUTS",
    "DecisionRefusedError",
    "DecisionVotesClient",
    "problem_code_of",
]
