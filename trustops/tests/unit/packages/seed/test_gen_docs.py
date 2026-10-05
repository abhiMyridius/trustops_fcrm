"""``fcrm_seed.gen_docs``: the three synthetic demo PDFs and ``facts.json``.

The committed files under ``evals/fixtures/documents`` are the artefacts; these
tests prove (1) the generator reproduces them byte for byte, (2) the real H3
extraction reads them back with the page counts and the page:line anchors
``facts.json`` promises, and (3) the layout rules that make (2) possible.
"""

from __future__ import annotations

import asyncio
import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from fcrm_ingestion_worker.anchors import compute_page_line_offsets, resolve_citation
from fcrm_ingestion_worker.jobs.ingest_document import (
    IngestDocumentPayload,
    IngestResult,
    handle,
)
from fcrm_seed import gen_docs
from fcrm_seed.docs_content import (
    BUSINESS_CASE_FILE,
    CONTRACT_FILE,
    SCOPE_LETTER_FILE,
    document_specs,
)
from fcrm_seed.docs_layout import (
    DocumentLayoutError,
    DocumentSpec,
    Fact,
    Heading,
    Page,
    Paragraph,
    Table,
    block_first_lines,
    fact_rows,
    render_pdf,
    validate_facts,
    wrap_text,
)
from fcrm_seed.loader import demo_case

DOCS_DIR = Path(__file__).resolve().parents[4] / "evals" / "fixtures" / "documents"
#: What the demo's upload passes to Guard 1 so seeded names survive masking.
KEEP_TERMS = [
    "Priya Nair",
    "Rahul Iyer",
    "Ana Mendes",
    "Joseph Okafor",
    "Lin Chen",
    "Remesas del Norte S.A.",
]


@cache
def _extract(name: str) -> IngestResult:
    """Run the real H3 job over a committed PDF, once per document."""
    payload = IngestDocumentPayload(
        case_id="FCRM-2026-0151",
        doc_id=name,
        path=str(DOCS_DIR / name),
        keep_terms=KEEP_TERMS,
    )
    return asyncio.run(handle(payload))


def _facts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (DOCS_DIR / "facts.json").read_text(encoding="utf-8")
    )
    return rows


def _result(name: str) -> IngestResult:
    return _extract(name)


# --------------------------------------------------------------- determinism
def test_regenerated_files_are_byte_identical_to_the_committed_ones() -> None:
    outputs = gen_docs.build_all(document_specs())
    assert sorted(outputs) == sorted(p.name for p in DOCS_DIR.iterdir())
    for name, data in outputs.items():
        assert (DOCS_DIR / name).read_bytes() == data, f"{name} is stale"


def test_rendering_twice_gives_identical_bytes() -> None:
    spec = document_specs()[2]
    assert render_pdf(spec) == render_pdf(spec)


def test_pdfs_carry_fixed_metadata_and_no_clock() -> None:
    for name in (BUSINESS_CASE_FILE, CONTRACT_FILE, SCOPE_LETTER_FILE):
        head = (DOCS_DIR / name).read_bytes()
        assert b"(D:20000101000000+00'00')" in head
        assert b"synthetic" in head.lower()


def test_check_flag_reports_a_stale_file_and_accepts_a_fresh_folder(
    tmp_path: Path,
) -> None:
    assert gen_docs.main(["--out-dir", str(tmp_path)]) == 0
    assert gen_docs.main(["--out-dir", str(tmp_path), "--check"]) == 0
    (tmp_path / "facts.json").write_text("[]\n", encoding="utf-8")
    assert gen_docs.main(["--out-dir", str(tmp_path), "--check"]) == 1
    (tmp_path / CONTRACT_FILE).unlink()
    assert gen_docs.main(["--out-dir", str(tmp_path), "--check"]) == 1


# ------------------------------------------------------- the real extraction
@pytest.mark.parametrize(
    ("name", "pages"),
    [(BUSINESS_CASE_FILE, 14), (CONTRACT_FILE, 9), (SCOPE_LETTER_FILE, 2)],
)
def test_extraction_reads_the_expected_page_count(name: str, pages: int) -> None:
    result = _result(name)
    assert result.status == "COMPLETE"
    assert result.page_count == pages


def test_page_counts_agree_with_the_seed_documents() -> None:
    seeded = {d["name"]: d["pages"] for d in demo_case()["documents"]}
    for name in (BUSINESS_CASE_FILE, CONTRACT_FILE, SCOPE_LETTER_FILE):
        assert _result(name).page_count == seeded[name]


def test_every_fact_is_found_at_its_page_and_line() -> None:
    rows = _facts()
    assert rows
    for row in rows:
        markdown = _result(row["doc"]).markdown
        cited = resolve_citation(markdown, row["page"], row["line"], row["line"])
        assert row["quote"] in cited, (row["field_id"], row["page"], row["line"])


def test_extracted_line_counts_match_the_block_structure_of_every_page() -> None:
    for spec in document_specs():
        offsets = compute_page_line_offsets(_result(spec.filename).markdown)
        for number, page in enumerate(spec.pages, start=1):
            last_start = block_first_lines(page)[-1]
            last_len = _block_len(page.blocks[-1])
            start, end = offsets[number]
            assert end - start + 1 == last_start + last_len - 1, (
                spec.filename,
                number,
            )


def _block_len(block: Any) -> int:
    return 2 + len(block.rows) if isinstance(block, Table) else 1


def test_committed_facts_equal_the_generators_prediction() -> None:
    predicted = [
        row.model_dump(mode="json")
        for spec in document_specs()
        for row in fact_rows(spec)
    ]
    assert _facts() == predicted


def test_facts_split_into_the_seed_candidate_counts() -> None:
    seeded = {d["name"]: d.get("candidates") for d in demo_case()["documents"]}
    intake = [r for r in _facts() if r["stage"] == "intake"]
    for name in (BUSINESS_CASE_FILE, CONTRACT_FILE):
        assert len([r for r in intake if r["doc"] == name]) == seeded[name]
    scope = [r for r in _facts() if r["doc"] == SCOPE_LETTER_FILE]
    assert scope
    assert {r["stage"] for r in scope} == {"clarification_v2"}


def test_every_extracted_seed_field_has_a_fact_in_its_cited_document() -> None:
    extracted = [f for f in demo_case()["fields"] if f["how"] == "extracted"]
    by_field = {r["field_id"]: r for r in _facts() if r["field_id"]}
    assert {f["id"] for f in extracted} <= set(by_field)
    for seeded in extracted:
        in_contract = seeded["source"].startswith("contract")
        expected = CONTRACT_FILE if in_contract else BUSINESS_CASE_FILE
        assert by_field[seeded["id"]]["doc"] == expected, seeded["id"]


# The walkthrough's own citations ("case p.1 l.3", "contract p.7 §9" ...): the
# page always matches, the line wherever the extractor's grammar can hold it.
_INFEASIBLE_LINES = {"case p.1 l.2"}


def _seed_anchor(source: str) -> tuple[int, int | None]:
    page = int(source.split(" p.")[1].split()[0])
    line = int(source.split(" l.")[1]) if " l." in source else None
    return page, line


def test_seed_citations_are_honoured_where_the_extractor_allows() -> None:
    cited = [r for r in _facts() if r["seed_source"]]
    assert {r["seed_source"] for r in cited} >= {
        "case p.1 l.3",
        "case p.1 l.9",
        "case p.2 l.8",
        "case p.4 l.12",
        "case p.6 tbl 2",
        "contract p.1",
        "contract p.3 §4",
        "contract p.7 §9",
    }
    for row in cited:
        page, line = _seed_anchor(row["seed_source"])
        assert row["page"] == page, row["seed_source"]
        if line is not None and row["seed_source"] not in _INFEASIBLE_LINES:
            assert row["line"] == line, row["seed_source"]


def test_line_two_of_a_page_is_structure_so_that_seed_citation_cannot_hold() -> None:
    markdown = _result(BUSINESS_CASE_FILE).markdown
    assert resolve_citation(markdown, 1, 2, 2) == "|---|---|"
    assert resolve_citation(markdown, 3, 2, 2) == ""


# ------------------------------------------------------------------- Guard 1
def test_guard1_masks_three_values_in_the_case_and_two_in_the_contract() -> None:
    case = _result(BUSINESS_CASE_FILE).guard1
    contract = _result(CONTRACT_FILE).guard1
    letter = _result(SCOPE_LETTER_FILE).guard1
    assert case is not None
    assert contract is not None
    assert letter is not None
    assert (case["status"], case["pii_total"]) == ("masked", 3)
    assert (contract["status"], contract["pii_total"]) == ("masked", 2)
    assert contract["pii_counts"] == {"PERSON": 2}
    assert (letter["status"], letter["pii_total"]) == ("clean", 0)


def test_kept_names_and_the_partner_survive_masking() -> None:
    markdown = _result(BUSINESS_CASE_FILE).markdown
    assert "Priya Nair, Payments" in markdown
    assert "Remesas del Norte S.A." in markdown
    assert "priya.nair@bank.example" not in markdown


def test_running_header_and_footer_are_stripped_from_long_documents() -> None:
    for name in (BUSINESS_CASE_FILE, CONTRACT_FILE):
        markdown = _result(name).markdown
        assert "SYNTHETIC" not in markdown
        assert "Synthetic demonstration document" not in markdown


# ----------------------------------------------------- what the documents say
def test_cash_pickup_is_absent_from_the_two_intake_documents() -> None:
    """Scene 6 flags the owner's cash-pickup answer as a new fact because the
    business case says bank deposit only; the contract excludes cash pickup."""
    case = _result(BUSINESS_CASE_FILE).markdown.lower()
    assert "cash pickup" not in case
    assert "cash-pickup" not in case
    assert "bank deposit only" in case
    contract = _result(CONTRACT_FILE).markdown
    assert "(bank deposit)" in contract
    assert "Cash pickup, mobile wallet payout and home delivery" in contract


def test_reused_and_owner_answered_values_appear_in_no_intake_document() -> None:
    text = (
        _result(BUSINESS_CASE_FILE).markdown + _result(CONTRACT_FILE).markdown
    ).lower()
    for absent in ("$2,999", "disclosure v3", "rule set v3", "cnbv", "62,000"):
        assert absent not in text


def test_the_scope_letter_carries_the_clarification_facts() -> None:
    markdown = _result(SCOPE_LETTER_FILE).markdown
    assert "38 cash-pickup agent locations in 6 states" in markdown
    assert "KYC standard v4" in markdown
    assert "62,000" in markdown
    assert "November 2026" in markdown


# ------------------------------------------------------------- layout rules
def _spec(*blocks: Any) -> DocumentSpec:
    return DocumentSpec(
        filename="x.pdf",
        title="t",
        subject="s",
        pages=(Page(tuple(blocks)),),
    )


def test_a_paragraph_that_starts_with_a_digit_is_rejected() -> None:
    with pytest.raises(DocumentLayoutError, match="digit"):
        render_pdf(_spec(Paragraph("2027 is the target year for launch.")))


def test_wrapping_never_starts_a_line_with_a_digit() -> None:
    text = " ".join(f"word{i} {i * 1000 + 12}" for i in range(60))
    lines = wrap_text(text, "Helvetica", 10.0, 200.0)
    assert len(lines) > 3
    assert not any(line[:1].isdigit() for line in lines)
    assert " ".join(lines) == text


def test_a_page_that_overflows_is_rejected() -> None:
    blocks = [Paragraph("A short sentence of ordinary prose.")] * 40
    with pytest.raises(DocumentLayoutError, match="overflows"):
        render_pdf(_spec(*blocks))


def test_a_table_cell_that_would_wrap_is_rejected() -> None:
    table = Table(("A", "B"), (("x", "y" * 200),), (100.0, 200.0))
    with pytest.raises(DocumentLayoutError, match="one line"):
        render_pdf(_spec(table))


def test_a_heading_that_would_not_read_as_one_is_rejected() -> None:
    with pytest.raises(DocumentLayoutError, match="heading"):
        render_pdf(_spec(Heading("Ends like a sentence.")))


def test_text_outside_the_standard_encoding_is_rejected() -> None:
    with pytest.raises(DocumentLayoutError, match="cp1252"):
        render_pdf(_spec(Paragraph("Corridor US → Mexico")))


def test_a_quote_that_is_not_on_its_line_is_rejected() -> None:
    fact = Fact("F99", "x", "v", "not in the paragraph")
    with pytest.raises(DocumentLayoutError, match="not in"):
        validate_facts(_spec(Paragraph("Some other text.", (fact,))))


def test_block_lines_follow_the_markdown_renderer() -> None:
    page = Page(
        (
            Table(("A",), (("1",), ("2",)), (100.0,)),
            Paragraph("p"),
            Heading("h"),
        )
    )
    assert block_first_lines(page) == [1, 6, 8]
