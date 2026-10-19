"""JSON a model wrote as text: what is mended, and what is refused.

The mends exist because a list sent as one JSON string failed the whole answer on
a slip in a verbatim quote (FCRM-2026-0154, 19 October). The rule they keep: a
decoded string is exactly what the model wrote, character for character; what
cannot be decoded that way is refused, never guessed at.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fcrm_agents.json_text import JSONTextError, loads_text, looks_like_json, mend

QUOTE = 'Agents must keep "know your customer" records\nfor five years.\tSee 4.2.'


def _hand_written(quote: str) -> str:
    """A list as a model writes it by hand: the quote dropped in unescaped."""
    return '[{"quote": "' + quote + '", "confidence": 0.9}]'


@pytest.mark.parametrize(
    "value",
    [
        [{"quote": QUOTE, "n": 1, "ok": True, "none": None}],
        {"a": 'say "hi"\\ there', "b": [1.5, -2, "x/y"]},
        "plain é ’ text",
        [],
    ],
)
def test_valid_json_decodes_exactly_as_the_standard_decoder_does(value: Any) -> None:
    text = json.dumps(value)
    assert loads_text(text) == json.loads(text)
    assert mend(text) == text


def test_an_unescaped_quote_mark_and_raw_line_breaks_decode_as_written() -> None:
    """The live failure: raw line breaks (D-115) and an unescaped ``"`` together."""
    (item,) = loads_text(_hand_written(QUOTE))
    assert item["quote"] == QUOTE


def test_an_escape_json_does_not_have_keeps_the_character() -> None:
    (item,) = loads_text(_hand_written("the customer\\'s agent"))
    assert item["quote"] == "the customer's agent"


@pytest.mark.parametrize(
    "text",
    [
        '[{"a": 1,}]',
        '[{"a": 1}, ]',
        '[{"a": [1, 2,],}]',
    ],
)
def test_a_trailing_comma_is_dropped(text: str) -> None:
    assert loads_text(text) in ([{"a": 1}], [{"a": [1, 2]}])


def test_a_comma_inside_a_string_is_kept() -> None:
    assert loads_text('[{"a": "x,]", "b": "y,}",}]') == [{"a": "x,]", "b": "y,}"}]


@pytest.mark.parametrize(
    "fence", ["```json\n{}\n```", "```\n{}\n```", "  ```JSON\n{}```  "]
)
def test_a_markdown_fence_around_the_whole_answer_is_dropped(fence: str) -> None:
    assert loads_text(fence.replace("{}", '[{"a": 1}]')) == [{"a": 1}]


def test_text_cut_off_mid_item_is_refused_with_where_it_broke() -> None:
    with pytest.raises(JSONTextError) as caught:
        loads_text('[{"quote": "Agents must keep records\nfor five')
    message = str(caught.value)
    assert "sent as text, not as a JSON array" in message
    assert "line " in message
    assert "column " in message
    assert "control character" not in message


def test_a_quote_mark_followed_by_a_comma_is_ambiguous_and_refused() -> None:
    """``"customer", as defined`` cannot be told from the end of the string: the
    safe answer is to refuse, not to pick one reading."""
    with pytest.raises(JSONTextError):
        loads_text(_hand_written('the "customer", as defined'))


def test_a_mend_never_drops_or_changes_a_character_of_a_string() -> None:
    hand = _hand_written(QUOTE)
    mended = mend(hand)
    assert mended.replace('\\"', '"') == hand


@pytest.mark.parametrize(
    ("text", "expected"),
    [("[1]", True), ('  {"a": 1}', True), ("```json", True), ("no list", False)],
)
def test_looks_like_json(text: str, expected: bool) -> None:
    assert looks_like_json(text) is expected
