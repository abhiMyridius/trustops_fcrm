"""The price table of ``ai/models/tiers.yaml`` is complete, carries its
provenance, and drives the cost estimate.

The figures asserted here are the ones read from the AWS Price List API on
6 Oct 2026, in ``us-east-1``: the Regional rate card
that the ``us.`` geographic cross-Region inference profiles bill at. A price
change is a deliberate edit of the file and of these constants together.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fcrm_evals.usage import (
    STATUS_PARTLY_VERIFIED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    PriceTable,
    UsageMeter,
)

TIERS_YAML = Path(__file__).parents[4] / "ai" / "models" / "tiers.yaml"
ONE_MILLION = 1_000_000

#: model label -> (input, output, cache_read, cache_write) USD per 1M tokens.
VERIFIED_PRICES: dict[str, tuple[float, float, float | None, float | None]] = {
    "Haiku 4.5": (1.10, 5.50, 0.11, 1.375),
    "Sonnet 5.5": (2.20, 11.00, 0.22, 2.75),
    "Nova Lite": (0.06, 0.24, 0.015, 0.0),
    "Nova Pro": (0.80, 3.20, 0.20, 0.0),
    "Titan Text Embeddings V2": (0.02, 0.0, None, None),
}


def _models_used(config: dict[str, Any]) -> set[str]:
    """Every model id a tier can call, plus the embeddings model."""
    used = {config["embeddings"]["model"]}
    for tier in config["tiers"].values():
        used.add(tier["primary"])
        used.update(tier.get("fallbacks", []))
    return used


def _id_of(prices: PriceTable, label: str) -> str:
    return next(m for m in prices.prices if prices.label(m) == label)


def test_every_model_a_tier_uses_has_a_price_row_and_a_status(
    tiers_config: dict[str, Any],
) -> None:
    rows = tiers_config["prices_usd_per_mtok"]["rows"]
    unpriced = sorted(_models_used(tiers_config) - set(rows))
    assert not unpriced, f"models used by a tier with no price row: {unpriced}"
    missing_status = sorted(m for m, row in rows.items() if not row.get("status"))
    assert not missing_status, f"price rows with no status: {missing_status}"


def test_no_price_is_a_placeholder_unless_it_is_unverified_with_a_reason(
    tiers_config: dict[str, Any],
) -> None:
    table = tiers_config["prices_usd_per_mtok"]
    assert not str(table["status"]).startswith("placeholder")
    for model_id, row in table["rows"].items():
        status = str(row["status"])
        assert status in {STATUS_VERIFIED, STATUS_UNVERIFIED}, (model_id, status)
        if status == STATUS_UNVERIFIED:
            assert str(row.get("reason", "")).strip(), f"{model_id}: no reason"
    assert "placeholder_" not in TIERS_YAML.read_text(encoding="utf-8")


def test_a_verified_row_names_its_source_and_the_day_it_was_read(
    tiers_config: dict[str, Any],
) -> None:
    for model_id, row in tiers_config["prices_usd_per_mtok"]["rows"].items():
        if row["status"] != STATUS_VERIFIED:
            continue
        assert str(row["source"]).strip(), f"{model_id}: no source"
        date.fromisoformat(str(row["retrieved_on"]))
        assert row["input"] > 0, model_id


def test_the_tables_status_is_the_roll_up_of_its_rows(
    tiers_config: dict[str, Any], price_table: PriceTable
) -> None:
    assert tiers_config["prices_usd_per_mtok"]["status"] == price_table.status


def test_the_table_holds_the_verified_aws_figures(price_table: PriceTable) -> None:
    assert price_table.status == STATUS_VERIFIED
    assert price_table.unverified == []
    assert price_table.region == "us-east-1"
    found = {
        price_table.label(model_id): (
            p.input,
            p.output,
            p.cache_read,
            p.cache_write,
        )
        for model_id, p in price_table.prices.items()
    }
    assert found == VERIFIED_PRICES


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # 500k uncached input, 400k read, 100k written, 100k output, by hand.
        ("Haiku 4.5", 0.5 * 1.10 + 0.4 * 0.11 + 0.1 * 1.375 + 0.1 * 5.50),
        ("Sonnet 5.5", 0.5 * 2.20 + 0.4 * 0.22 + 0.1 * 2.75 + 0.1 * 11.00),
        ("Nova Lite", 0.5 * 0.06 + 0.4 * 0.015 + 0.1 * 0.0 + 0.1 * 0.24),
        ("Nova Pro", 0.5 * 0.80 + 0.4 * 0.20 + 0.1 * 0.0 + 0.1 * 3.20),
    ],
)
def test_cost_uses_each_models_four_rates(
    price_table: PriceTable, label: str, expected: float
) -> None:
    cost = price_table.cost(
        _id_of(price_table, label),
        input_tokens=ONE_MILLION,
        output_tokens=100_000,
        cache_read_tokens=400_000,
        cache_write_tokens=100_000,
    )
    assert cost == pytest.approx(expected)


def test_known_token_counts_give_the_known_dollar_amounts(
    price_table: PriceTable,
) -> None:
    """Spot checks written out as dollars, so a wrong rate cannot hide in a sum."""
    sonnet = _id_of(price_table, "Sonnet 5.5")
    assert price_table.cost(sonnet, input_tokens=ONE_MILLION) == pytest.approx(2.20)
    assert price_table.cost(sonnet, output_tokens=ONE_MILLION) == pytest.approx(11.0)
    assert price_table.cost(
        sonnet, input_tokens=ONE_MILLION, cache_read_tokens=ONE_MILLION
    ) == pytest.approx(0.22)
    assert price_table.cost(
        sonnet, input_tokens=ONE_MILLION, cache_write_tokens=ONE_MILLION
    ) == pytest.approx(2.75)
    nova_pro = _id_of(price_table, "Nova Pro")
    assert price_table.cost(nova_pro, input_tokens=ONE_MILLION) == pytest.approx(0.80)
    assert price_table.cost(nova_pro, output_tokens=ONE_MILLION) == pytest.approx(3.2)
    nova = _id_of(price_table, "Nova Lite")
    # AWS lists the Nova cache write at no charge: it is not the input price.
    assert price_table.cost(
        nova, input_tokens=ONE_MILLION, cache_write_tokens=ONE_MILLION
    ) == pytest.approx(0.0)
    titan = _id_of(price_table, "Titan Text Embeddings V2")
    assert price_table.cost(titan, input_tokens=ONE_MILLION) == pytest.approx(0.02)


def test_the_usage_meter_prices_each_call_at_its_own_model(
    price_table: PriceTable,
) -> None:
    """A run that falls back mid-row is costed call by call."""
    meter = UsageMeter(price_table)
    meter.record(
        _id_of(price_table, "Sonnet 5.5"),
        input_tokens=ONE_MILLION,
        output_tokens=ONE_MILLION,
    )
    meter.record(
        _id_of(price_table, "Nova Pro"),
        input_tokens=ONE_MILLION,
        output_tokens=ONE_MILLION,
    )
    assert meter.cost_usd == pytest.approx(2.20 + 11.0 + 0.80 + 3.20)
    assert meter.as_usage().cost_usd == pytest.approx(17.2)


def test_a_verified_table_says_so_with_its_region_and_date(
    price_table: PriceTable,
) -> None:
    note = price_table.note()
    assert re.fullmatch(
        r"verified AWS list prices, us-east-1, retrieved \d{4}-\d{2}-\d{2}", note
    )
    assert "placeholder" not in note


def _with_unverified(config: dict[str, Any], *labels: str) -> PriceTable:
    """The real table with the named models (by label) marked unverified."""
    changed = copy.deepcopy(config)
    names = PriceTable.from_tiers(config)
    for model_id, row in changed["prices_usd_per_mtok"]["rows"].items():
        if names.label(model_id) in labels:
            row["status"] = STATUS_UNVERIFIED
            row["reason"] = "the Price List has no row for it in this region"
    return PriceTable.from_tiers(changed)


def test_a_table_with_an_unverified_model_names_it_by_label(
    tiers_config: dict[str, Any],
) -> None:
    prices = _with_unverified(tiers_config, "Nova Pro")
    assert prices.status == STATUS_PARTLY_VERIFIED
    assert prices.unverified == ["Nova Pro"]
    note = prices.note()
    assert note.startswith("unverified for: Nova Pro;")
    assert "retrieved" in note
    assert "us.amazon" not in note


def test_a_table_with_no_verified_row_is_unverified_throughout(
    tiers_config: dict[str, Any], price_table: PriceTable
) -> None:
    prices = _with_unverified(tiers_config, *VERIFIED_PRICES)
    assert prices.status == STATUS_UNVERIFIED
    assert prices.unverified == sorted(VERIFIED_PRICES)
    assert prices.note().endswith("(every model), us-east-1")
    assert price_table.status == STATUS_VERIFIED  # the real table is untouched


def test_a_table_without_any_status_is_not_called_verified() -> None:
    prices = PriceTable.from_tiers(
        {"prices_usd_per_mtok": {"rows": {"m": {"input": 1.0, "output": 2.0}}}}
    )
    assert prices.status == STATUS_UNVERIFIED
    assert "verified AWS" not in prices.note()
    empty = PriceTable.from_tiers({})
    assert "not recorded" in empty.note()
