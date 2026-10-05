"""fcrm_contracts.api.identity: the login contract the web app is built on."""

from __future__ import annotations

import pytest
from fcrm_contracts.api.identity import (
    DemoUser,
    DemoUserGroup,
    LoginRequest,
    LoginResponse,
    PrincipalOut,
)
from fcrm_contracts.enums import Role
from pydantic import ValidationError


def test_login_request_accepts_a_username():
    request = LoginRequest.model_validate({"username": "riyer", "password": "x"})
    assert request.login == "riyer"


def test_login_request_accepts_an_email():
    request = LoginRequest.model_validate({"email": "u-riyer", "password": "x"})
    assert request.login == "u-riyer"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "riyer", "email": "u-riyer", "password": "x"},
        {"password": "x"},
    ],
    ids=["both", "neither"],
)
def test_login_request_needs_exactly_one_account_field(body: dict[str, str]):
    with pytest.raises(ValidationError) as info:
        LoginRequest.model_validate(body)
    assert "exactly one" in str(info.value)


def test_login_request_allows_an_empty_password_for_the_demo_picker():
    assert (
        LoginRequest.model_validate({"username": "riyer", "password": ""}).password
        == ""
    )


@pytest.mark.parametrize(
    "body",
    [
        {"username": "", "password": "x"},
        {"username": "a" * 255, "password": "x"},
        {"username": "riyer", "password": "p" * 1025},
        {"username": "riyer"},
        {"username": "riyer", "password": "x", "otp": "123456"},
    ],
    ids=["blank", "long-login", "long-password", "no-password", "extra-field"],
)
def test_login_request_rejects_a_malformed_body(body: dict[str, str]):
    with pytest.raises(ValidationError):
        LoginRequest.model_validate(body)


def test_login_response_defaults_the_token_type_to_bearer():
    user = PrincipalOut(
        id="u-riyer", name="Rahul Iyer", roles=[Role.ANALYST], title="FCRM Analyst"
    )
    response = LoginResponse(access_token="t", expires_in=900, user=user)
    assert response.token_type == "bearer"
    assert response.user.unit is None


def test_login_response_refuses_another_token_type():
    user = PrincipalOut(
        id="u-riyer", name="Rahul Iyer", roles=[Role.ANALYST], title="FCRM Analyst"
    )
    with pytest.raises(ValidationError):
        LoginResponse(access_token="t", token_type="mac", expires_in=900, user=user)  # type: ignore[arg-type]


def test_login_response_has_no_refresh_token_field():
    assert "refresh_token" not in LoginResponse.model_fields


def test_demo_picker_models_round_trip():
    group = DemoUserGroup(
        role=Role.PO,
        label="Product owners",
        users=[
            DemoUser(
                username="pnair",
                name="Priya Nair",
                role=Role.PO,
                title="Product Owner",
                unit="Payments",
            )
        ],
    )
    assert DemoUserGroup.model_validate(group.model_dump(mode="json")) == group
