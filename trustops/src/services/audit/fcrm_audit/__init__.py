"""S4 audit-service.

``fcrm_audit.chain`` is pure and has no I/O: it is the only place a
hash-chained audit record is ever built or verified anywhere in this
system. The audit trail is immutable (brief requirement) and insert-only
by construction — the repository layer this package's HTTP routes use has
no update or delete method for ``audit_records`` at all, and the Mongo user
it connects as (``fcrm_audit_writer``, design A §11.1 ``02-users.js``) is
granted only ``insert`` and ``find`` at the database level, so even a bug
in this code cannot mutate history.
"""

from __future__ import annotations
