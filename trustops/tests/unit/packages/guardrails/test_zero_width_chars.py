"""``ZERO_WIDTH_CHARS`` is spelled with escapes, not literal characters (S6389)."""

from __future__ import annotations

from fcrm_guardrails.textnorm import ZERO_WIDTH, ZERO_WIDTH_CHARS

EXPECTED = [
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,  # zero-width and direction marks
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi embeddings and overrides
    0x2060, 0x2061, 0x2062, 0x2063, 0x2064,  # word joiner, invisible operators
    0x2066, 0x2067, 0x2068, 0x2069,  # bidi isolates
    0xFEFF, 0x00AD, 0x034F, 0x180E,  # BOM, soft hyphen, CGJ, Mongolian separator
]  # fmt: skip


def test_the_zero_width_set_is_exactly_the_documented_code_points() -> None:
    assert [ord(c) for c in ZERO_WIDTH_CHARS] == EXPECTED


def test_every_listed_code_point_is_stripped_and_nothing_else() -> None:
    assert ZERO_WIDTH.sub("", "a" + ZERO_WIDTH_CHARS + "b") == "ab"
    assert ZERO_WIDTH.sub("", "a b-c") == "a b-c"
