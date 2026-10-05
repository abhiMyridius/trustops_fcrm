"""X1 identity: login, refresh, the caller's own profile, the user roster and
the local demo picker, plus the decoded-principal shape used by
``/core/validate`` (kept for the nginx ``auth_request`` path; design A §8.1).

The web app's route names are the contract (``src/frontend/src/api/endpoints.ts``):
``POST /auth/login {username|email, password}`` answers
``{access_token, token_type, expires_in, user}`` and ``GET /auth/me`` answers
the same ``user`` shape (:class:`PrincipalOut`). Optional fields that are
``None`` are left out of the JSON body (the routes set
``response_model_exclude_none``), so ``unit`` is simply absent for a person
with no unit, which is what the web's ``Principal.unit?`` expects.
"""

from __future__ import annotations

from typing import Literal, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fcrm_contracts.enums import Role


class LoginRequest(BaseModel):
    """Credentials for ``POST /login``.

    Exactly one of ``username`` (the web form's field; the seed id without its
    ``u-`` prefix) or ``email`` (the original contract field; either spelling
    of the seed id) names the account. ``password`` may be empty only for the
    local demo picker (see ``fcrm_identity.settings.IdentitySettings
    .demo_login_active``); everywhere else an empty password is just a wrong
    one.
    """

    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = Field(default=None, min_length=1, max_length=254)
    email: Optional[str] = Field(default=None, min_length=1, max_length=254)
    password: str = Field(max_length=1024)

    @model_validator(mode="after")
    def _exactly_one_login_field(self) -> Self:
        """Reject a body that names the account twice or not at all (a 422)."""
        if (self.username is None) == (self.email is None):
            raise ValueError("Provide exactly one of 'username' or 'email'.")
        return self

    @property
    def login(self) -> str:
        """The single login string the body carries, whichever field it used."""
        resolved = self.username if self.username is not None else self.email
        # The validator above guarantees exactly one field is set.
        return resolved if resolved is not None else ""


class PrincipalOut(BaseModel):
    """A person as the web shows them: the login ``user`` and ``GET /me``.

    ``id`` is the seed id (``u-riyer``), which is the JWT ``sub`` and the key
    every other service stores (``analyst_id``, ``owner``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    roles: list[Role]
    title: str
    unit: Optional[str] = None


class LoginResponse(BaseModel):
    """What every login-family route answers with.

    The refresh token is an httpOnly cookie and never appears in the body.
    """

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds; 900 (15 min)
    user: PrincipalOut


class RosterUser(BaseModel):
    """One entry of ``GET /users?role=``: who can be assigned or can vote.

    Deliberately narrower than :class:`PrincipalOut`: no title, no account
    flags, never a credential.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    roles: list[Role]
    unit: Optional[str] = None


class DemoUser(BaseModel):
    """One person on the local demo picker (``GET /auth/demo-users``)."""

    model_config = ConfigDict(extra="forbid")

    username: str
    name: str
    role: Role
    title: str
    unit: Optional[str] = None


class DemoUserGroup(BaseModel):
    """The demo picker's people of one role, with the heading the page shows."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    label: str
    users: list[DemoUser]


class ValidateResponse(BaseModel):
    """What ``/core/validate`` returns to nginx's ``auth_request`` and to any
    service that falls back to it when JWKS is unavailable.
    """

    model_config = ConfigDict(extra="forbid")

    valid: bool
    sub: str = ""
    roles: list[Role] = []
