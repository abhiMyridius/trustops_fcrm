"""S1 case-service.

``fcrm_case.domain`` is pure business logic — field-set resolution,
completion arithmetic, and the four submit checks — with no Mongo, no
HTTP, no agent in sight. The prototype computed completion three
different ways in three different places (docs/requirements/research/
04-prototype-fcrm-v2-notes.md); this package exists so there is exactly
one function that does it, imported everywhere a completion number is
needed, including by the web client's display logic (via the generated
API, never recomputed client-side).
"""

from __future__ import annotations
