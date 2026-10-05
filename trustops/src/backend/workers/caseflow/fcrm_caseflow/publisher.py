"""The :class:`fcrm_common.events.Publisher` adapter for H1.

The implementation is shared with every other FastStream process and lives
in :mod:`fcrm_common.broker`; this module only keeps the historical import
path (``fcrm_caseflow.publisher.FastStreamPublisher``).
"""

from __future__ import annotations

from fcrm_common.broker import FastStreamPublisher

__all__ = ["FastStreamPublisher"]
