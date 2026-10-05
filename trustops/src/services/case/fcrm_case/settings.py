"""S1's own settings: the shared contract plus the case-specific flags
``docker-compose.yaml``'s ``case:`` service block already sets.
"""

from __future__ import annotations

from fcrm_common.settings import BaseServiceSettings
from pydantic import Field


class CaseSettings(BaseServiceSettings):
    """``UPLOAD_MAX_BYTES``/``UPLOAD_ALLOWED_EXTENSIONS`` are declared here
    for forward compatibility with compose's ``case:`` environment block;
    no document-upload route lands in this pass (out of scope for the HTTP
    CRUD/field/submit/SSE/versions surface this settings class backs), so
    they are read by nothing yet. ``SSE_HEARTBEAT_S`` is live: it paces
    ``GET /cases/{case_id}/stream``'s poll loop. ``SSE_MAX_LIFETIME_S``
    (not in compose; defaults to one hour) caps how long one stream may
    run before the client's ``EventSource`` reconnects.
    """

    app_name: str = "fcrm-case"
    upload_max_bytes: int = 52_428_800
    upload_allowed_extensions: str = ".pdf,.docx,.xlsx,.pptx,.csv,.md,.txt"
    sse_heartbeat_s: float = Field(default=15.0, gt=0)
    sse_max_lifetime_s: float = Field(default=3600.0, gt=0)

    @property
    def upload_allowed_extensions_list(self) -> list[str]:
        """``upload_allowed_extensions`` split on commas, trimmed."""
        return [ext.strip() for ext in self.upload_allowed_extensions.split(",") if ext]
