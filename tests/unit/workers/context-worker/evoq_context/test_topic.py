"""Tests for ``chunking/topic.py`` — deterministic lexical-cohesion cuts.

``Span`` only requires internal consistency (``len(text) == end - start``);
it does not need to be a real slice of a shared source string, so these
tests build synthetic paragraph ``Span``s directly.
"""

from __future__ import annotations

from evoq_context.chunking.topic import TopicBoundaryRefiner, TopicConfig
from evoq_context.chunking.types import Span

_COOKING_SENTENCES = [
    "The chef seasoned the roasted chicken with rosemary garlic and thyme "
    "before searing it in a hot cast iron skillet over the open flame.",
    "A good stock simmers for hours with onion carrot celery and bay leaf "
    "until the broth turns rich golden and deeply flavourful for the soup.",
    "Fold the whisked egg whites gently into the chocolate batter so the "
    "souffle keeps its air and rises evenly in the buttered ramekin dish.",
    "The sourdough starter needs regular feeding with flour and water to "
    "keep the wild yeast active for a good rise in the crusty loaf of bread.",
    "Caramelising the onions slowly over low heat brings out a deep sweet "
    "flavour that pairs beautifully with the seared steak and pan sauce.",
    "Blanch the green beans briefly in boiling salted water then shock "
    "them in ice water to keep their bright colour and crisp fresh bite.",
]

_FINANCE_SENTENCES = [
    "The quarterly earnings report showed revenue growth driven by strong "
    "equity markets and rising bond yields across the fixed income desk.",
    "Analysts revised their forecast for the technology sector after the "
    "central bank signalled another rate hike to curb persistent inflation.",
    "The merger between the two regional banks was approved by regulators "
    "after months of antitrust review and shareholder capital negotiation.",
    "Portfolio managers rebalanced toward defensive equities as volatility "
    "in the futures market increased ahead of the upcoming earnings season.",
    "The company issued new corporate bonds to refinance existing debt at "
    "a lower coupon rate, improving its balance sheet and credit rating.",
    "Venture capital funding for early stage startups declined this "
    "quarter as investors grew cautious about valuations and burn rate.",
]


def _units(sentences: list[str], *, base: int = 0) -> list[Span]:
    spans: list[Span] = []
    pos = base
    for sentence in sentences:
        spans.append(Span(pos, pos + len(sentence), sentence, "prose"))
        pos += len(sentence) + 2  # pretend one blank line between paragraphs
    return spans


def test_refine_is_deterministic() -> None:
    units = _units(_COOKING_SENTENCES + _FINANCE_SENTENCES)
    refiner = TopicBoundaryRefiner(TopicConfig())

    first = refiner.refine(units)
    second = refiner.refine(units)

    assert first == second


def test_two_topic_document_gets_at_least_one_cut() -> None:
    units = _units(_COOKING_SENTENCES + _FINANCE_SENTENCES)
    refiner = TopicBoundaryRefiner(TopicConfig())

    cuts = refiner.refine(units)

    assert len(cuts) >= 1
    # The strongest cut should land at (or very near) the actual topic
    # switch between the two vocabularies.
    switch_index = len(_COOKING_SENTENCES)
    assert any(abs(cut - switch_index) <= 1 for cut in cuts)


def test_single_topic_document_gets_zero_cuts() -> None:
    # A single narrow topic, restated with heavy shared vocabulary each
    # time — no gap should stand out enough to exceed the depth threshold.
    base = (
        "The sourdough starter needs regular feeding with flour and water "
        "to keep the wild yeast active healthy and ready for the bread"
    )
    sourdough = [f"{base}, note number {i}." for i in range(10)]
    units = _units(sourdough)
    refiner = TopicBoundaryRefiner(TopicConfig())

    cuts = refiner.refine(units)

    assert cuts == []


def test_skips_when_below_min_paragraphs() -> None:
    units = _units(_COOKING_SENTENCES[:2])
    refiner = TopicBoundaryRefiner(TopicConfig(min_paragraphs=4))

    assert refiner.refine(units) == []


def test_disabled_config_never_cuts() -> None:
    units = _units(_COOKING_SENTENCES + _FINANCE_SENTENCES)
    refiner = TopicBoundaryRefiner(TopicConfig(enabled=False))

    assert refiner.refine(units) == []


def test_skips_gap_with_too_few_tokens_on_one_side() -> None:
    # First unit is tiny (a handful of tokens); the gap right after it must
    # not be evaluated (either side needs >= 20 tokens of signal).
    tiny = Span(0, 8, "Hi there", "prose")
    rest = _units(_FINANCE_SENTENCES, base=10)
    units = [tiny, *rest]
    refiner = TopicBoundaryRefiner(TopicConfig(window=1))

    cuts = refiner.refine(units)

    assert 1 not in cuts
