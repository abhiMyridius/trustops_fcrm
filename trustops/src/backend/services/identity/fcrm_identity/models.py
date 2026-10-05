"""Mongo-stored shapes for X1 (design A §2.6).

Only two collections are kept from the cloned service's three: ``users``
and ``refresh_tokens``. ``signing_keys`` (key rotation) is pruned — see
``UPSTREAM.md`` — this service loads its one RS256 keypair from
``JWT_KEY_FILE`` at startup instead.

Neither model carries a plaintext secret: ``UserRecord.password_hash`` is
always a bcrypt digest (never the plaintext password), and nothing here
represents an OTP at all (design A §8.1 fix 2: the OTP flow is removed,
password login replaces it).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fcrm_contracts.enums import Role
from pydantic import BaseModel, ConfigDict


class UserRecord(BaseModel):
    """One seeded person. ``id`` is the seed id (``u-riyer``) and is also
    the document's ``_id`` (see ``store.py``'s ``id_of``).

    ``title`` is the seed's ``role`` text ("FCRM Analyst"), shown under the
    person's name in the web app. It defaults to empty so a document written
    before the field existed still loads; the routes then fall back to the
    role's own label (``routes.title_for``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    roles: list[Role]
    title: str = ""
    unit: Optional[str] = None
    password_hash: str
    enabled: bool = True
    created_at: datetime


class RefreshTokenRecord(BaseModel):
    """One issued refresh token, keyed by its ``jti`` (also the ``_id``).

    ``exp`` backs a Mongo TTL index (``lifespan.py``), so an expired
    record disappears on its own; ``revoked`` is set explicitly on
    logout and on rotation (``POST /refresh`` issues a new token and
    revokes the one it was given, so a stolen refresh token cannot be
    replayed after its holder's next refresh).
    """

    model_config = ConfigDict(extra="forbid")

    jti: str
    sub: str
    exp: datetime
    revoked: bool = False
