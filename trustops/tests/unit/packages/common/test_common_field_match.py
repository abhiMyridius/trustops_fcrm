"""Tying a question that names no field to the field it asks about (D-128,
``fcrm_common.field_match``)."""

from __future__ import annotations

import pytest
from fcrm_common.field_match import field_for_question

LABELS = {
    "F01": "Change owner",
    "F06": "Partner or vendor identity",
    "F30": "Product sign-off reference",
    "F31": "Product sign-off date",
    "F40": "Geography",
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Who is the change owner?", "F01"),
        (
            "Who is the change owner for this fixed-term deposit product initiative?",
            "F01",
        ),
        ("What is the product sign-off reference?", "F30"),
        ("Please give the product sign off date.", "F31"),
        ("What is the partner or vendor identity?", "F06"),
        ("Please confirm the value of F30.", "F30"),
        ("Which geography does this cover?", None),  # one word: by id only
        ("Is there anything else we should know?", None),
        ("Who is the owner?", None),
    ],
)
def test_a_question_is_tied_to_the_field_it_asks_about(
    text: str, expected: str | None
) -> None:
    assert field_for_question(text, LABELS) == expected


def test_the_longest_label_wins() -> None:
    labels = {"F01": "Change owner", "F02": "Change owner email"}
    assert field_for_question("What is the change owner email?", labels) == "F02"


def test_two_fields_matched_equally_are_a_tie() -> None:
    labels = {"F01": "Launch date", "F02": "Review date"}
    assert field_for_question("Launch date or review date?", labels) is None


def test_an_id_not_in_the_labels_is_ignored() -> None:
    assert field_for_question("What about F99?", LABELS) is None
