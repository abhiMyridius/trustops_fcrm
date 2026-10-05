"""FastAPI dependencies that hand a route what ``lifespan.register`` (or a test)
put on ``app.state``.

A missing piece is a deployment fault, not a client error: the accessor logs it
and answers 503 (fail closed), the same way :func:`fcrm_common.auth.current_user`
treats a missing token verifier.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fcrm_common.errors import UpstreamUnavailableError

from fcrm_case.directory import AnalystLoadService, IdentityDirectory
from fcrm_case.outcome_store import OutcomeStore
from fcrm_case.settings import CaseSettings
from fcrm_case.sse import ActivityWriter, EventHub
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)


def _wired(request: Request, name: str) -> Any:
    """``app.state.<name>``, or a 503 when nothing wired it."""
    value = getattr(request.app.state, name, None)
    if value is None:
        logger.error("case service dependency not wired", extra={"dependency": name})
        raise UpstreamUnavailableError(
            f"The case service is not fully configured ({name}).", dependency=name
        )
    return value


def get_store(request: Request) -> CaseStore:
    """The case store ``lifespan.py`` wired onto ``app.state`` at startup (or
    an ``InMemoryCaseStore`` a test wired instead).
    """
    store: CaseStore = _wired(request, "store")
    return store


def get_settings(request: Request) -> CaseSettings:
    """The settings ``lifespan.py``/a test fixture wired onto ``app.state``."""
    settings: CaseSettings = _wired(request, "settings")
    return settings


def get_views(request: Request) -> ViewStore:
    """The view store (field values, documents, review items, intake views)."""
    views: ViewStore = _wired(request, "views")
    return views


def get_hub(request: Request) -> EventHub:
    """The in-process hub the case stream reads."""
    hub: EventHub = _wired(request, "hub")
    return hub


def get_directory(request: Request) -> IdentityDirectory:
    """The analyst roster (X1 over HTTP, cached)."""
    directory: IdentityDirectory = _wired(request, "directory")
    return directory


def get_loads(request: Request) -> AnalystLoadService:
    """The R5 load read (roster plus one aggregation, cached)."""
    loads: AnalystLoadService = _wired(request, "loads")
    return loads


def get_outcomes(request: Request) -> OutcomeStore:
    """The outcome, close-request and condition store."""
    outcomes: OutcomeStore = _wired(request, "outcomes")
    return outcomes


def get_writer(request: Request) -> ActivityWriter:
    """An activity writer over the store's log and the hub: persist an event,
    then publish it to the open streams.
    """
    return ActivityWriter(get_store(request).activity, get_hub(request))
