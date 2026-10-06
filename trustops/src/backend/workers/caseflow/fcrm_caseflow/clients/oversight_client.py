"""The two peers the oversight steps add to H1: S4's examiner (the answer H1
posts back) and S1's re-flags (the candidate match and the rows H1 asks for).

One method per route, typed with the ``fcrm_contracts`` models; every call has
the shared per-call deadline (:data:`fcrm_caseflow.clients.CALL_TIMEOUTS`) and
every write is idempotent (a fixed id, or an ``Idempotency-Key`` derived from the
step), because a step may run again after a failure (ADR-28).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit_oversight import ExaminerAnswerIn, ExaminerAnswerOut
from fcrm_contracts.api.case_internal_reflags import (
    ReflagCandidate,
    ReflagCandidates,
    ReflagsPost,
    ReflagsPosted,
)

from fcrm_caseflow.clients import CALL_TIMEOUTS, parse_body

logger = logging.getLogger(__name__)

AUDIT: Final = "audit"
CASE: Final = "case"

#: Route templates, relative to each service's mount prefix.
ANSWERS_PATH: Final = "/examiner/answers"
CANDIDATES_PATH: Final = "/internal/reflags/candidates"
REFLAGS_PATH: Final = "/internal/reflags"


class ExaminerClient:
    """Posts the examiner's checked answer to S4.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``AUDIT_SERVICE_URL``.
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def post_answer(
        self, answer: ExaminerAnswerIn, *, idempotency_key: str
    ) -> ExaminerAnswerOut:
        """``POST /examiner/answers``: store the answer to a question. The same
        answer again is a replay at S4; a different one for the question is a
        409, and an answer citing a record the chain does not hold is a 422.

        Raises:
            fcrm_common.errors.NotFoundError: S4 holds no such question.
            fcrm_common.errors.ValidationError: S4 refused the citations.
            fcrm_common.errors.ConflictError: A different answer is stored.
        """
        body = await self._client.request_json(
            "POST",
            ANSWERS_PATH,
            json=answer.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            timeout=CALL_TIMEOUTS,
        )
        return parse_body(ExaminerAnswerOut, body, dependency=AUDIT, route=ANSWERS_PATH)


class ReflagClient:
    """Asks S1 for the cases a list change may affect and holds the re-flags.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``CASE_SERVICE_URL``.
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def candidates(self, countries: Sequence[str]) -> list[ReflagCandidate]:
        """``GET /internal/reflags/candidates``: the open cases whose geography
        or corridor names any of ``countries``, one per case and country.

        Raises:
            fcrm_common.errors.FcrmError: S1 refused or was unreachable.
        """
        body = await self._client.request_json(
            "GET",
            CANDIDATES_PATH,
            params=[("countries", country) for country in countries],
            timeout=CALL_TIMEOUTS,
        )
        return parse_body(
            ReflagCandidates, body, dependency=CASE, route=CANDIDATES_PATH
        ).items

    async def post(self, post: ReflagsPost, *, idempotency_key: str) -> ReflagsPosted:
        """``POST /internal/reflags``: hold the re-flags, once each (their ids are
        derived, so a second post creates nothing).

        Raises:
            fcrm_common.errors.ValidationError: A re-flag names a case S1 does
                not hold.
        """
        body = await self._client.request_json(
            "POST",
            REFLAGS_PATH,
            json=post.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            timeout=CALL_TIMEOUTS,
        )
        return parse_body(ReflagsPosted, body, dependency=CASE, route=REFLAGS_PATH)


@dataclass(frozen=True)
class OversightClients:
    """What the oversight steps reach their peers through.

    Attributes:
        examiner: S4's answer route.
        reflags: S1's candidate and re-flag routes.
    """

    examiner: ExaminerClient
    reflags: ReflagClient
