"""fcrm_identity.directory: stored users -> the shapes the web reads."""

from __future__ import annotations

from fcrm_contracts.enums import Role
from fcrm_identity.directory import (
    demo_groups,
    principal_out,
    roster_user,
    title_for,
    username_of,
)
from identity_test_support import make_user


def test_username_of_strips_the_seed_prefix_only():
    assert username_of("u-riyer") == "riyer"
    assert username_of("riyer") == "riyer"
    assert username_of("u-u-odd") == "u-odd"


def test_title_for_prefers_the_stored_title():
    user = make_user("u-riyer", "Rahul Iyer", Role.ANALYST, title="Senior Analyst")
    assert title_for(user) == "Senior Analyst"


def test_title_for_falls_back_to_the_role_label_for_a_legacy_document():
    user = make_user("u-riyer", "Rahul Iyer", Role.ANALYST)
    assert user.title == ""
    assert title_for(user) == "FCRM Analyst"


def test_title_for_a_user_with_no_roles_is_empty():
    user = make_user("u-x", "No Roles", Role.PO).model_copy(update={"roles": []})
    assert title_for(user) == ""


def test_principal_out_carries_id_name_roles_title_and_unit():
    user = make_user(
        "u-pnair", "Priya Nair", Role.PO, title="Product Owner", unit="Payments"
    )
    out = principal_out(user)
    assert out.model_dump() == {
        "id": "u-pnair",
        "name": "Priya Nair",
        "roles": [Role.PO],
        "title": "Product Owner",
        "unit": "Payments",
    }


def test_principal_out_never_carries_the_password_hash():
    out = principal_out(make_user("u-pnair", "Priya Nair", Role.PO))
    assert "password_hash" not in out.model_dump()


def test_roster_user_is_narrower_than_a_principal():
    user = make_user("u-pnair", "Priya Nair", Role.PO, title="Product Owner")
    assert set(roster_user(user).model_dump()) == {"id", "name", "roles", "unit"}


def test_demo_groups_follow_the_picker_order_and_skip_empty_roles():
    users = [
        make_user("u-lchen", "Lin Chen", Role.COMMITTEE),
        make_user("u-riyer", "Rahul Iyer", Role.ANALYST),
        make_user("u-pnair", "Priya Nair", Role.PO),
    ]
    groups = demo_groups(users)
    assert [g.role for g in groups] == [Role.PO, Role.ANALYST, Role.COMMITTEE]
    assert [g.label for g in groups] == [
        "Product owners",
        "FCRM analysts",
        "Risk Committee",
    ]


def test_demo_groups_keep_the_given_order_within_a_group():
    users = [
        make_user("u-riyer", "Rahul Iyer", Role.ANALYST),
        make_user("u-mshah", "Meera Shah", Role.ANALYST),
        make_user("u-dbrooks", "Daniel Brooks", Role.ANALYST),
    ]
    (group,) = demo_groups(users)
    assert [u.username for u in group.users] == ["riyer", "mshah", "dbrooks"]


def test_demo_groups_leave_out_a_role_the_picker_does_not_show():
    assert demo_groups([make_user("u-svc", "Svc", Role.SERVICE)]) == []


def test_demo_groups_of_nobody_is_empty():
    assert demo_groups([]) == []
