"""X1 identity: login, refresh, and the decoded-principal shape used by
``/core/validate`` (kept for the nginx ``auth_request`` path; design A §8.1).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    expires_in: int  # seconds; 900 (15 min)
    # refresh token is set as an httpOnly cookie, never returned in the body


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub: str
    name: str
    roles: list[Role]


class ValidateResponse(BaseModel):
    """What ``/core/validate`` returns to nginx's ``auth_request`` and to any
    service that falls back to it when JWKS is unavailable.
    """

    model_config = ConfigDict(extra="forbid")

    valid: bool
    sub: str = ""
    roles: list[Role] = []
