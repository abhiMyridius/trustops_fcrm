"""Contract lock: this repo's own copy of the golden fixtures under
``tests/fixtures/contracts/`` (mirrored from ``AEO/contracts/`` — see
``AEO/contracts/README.md``) — validated against ``profile_hints.schema.json``
via ``jsonschema``, and checked for the exact marker grammar per source_type
(``markers.md``). If ``jsonschema`` isn't installed, the schema-validation
test is skipped rather than failing the whole suite.

These fixtures are self-contained (copied, not read from a sibling
``AEO/contracts/`` directory) so this repo's test suite never depends on
being checked out next to the others.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts"

jsonschema = pytest.importorskip(
    "jsonschema", reason="jsonschema not installed — schema validation skipped"
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class TestProfileHintsSchema:
    @pytest.mark.parametrize(
        "fixture",
        [
            "canonical_document.confluence.json",
            "canonical_document.jira.json",
            "canonical_document.file.json",
        ],
    )
    def test_profile_hints_validates_against_schema(self, fixture: str) -> None:
        schema = _load("profile_hints.schema.json")
        doc = _load(fixture)
        jsonschema.validate(instance=doc["profile_hints"], schema=schema)


class TestCanonicalDocumentShape:
    @pytest.mark.parametrize(
        "fixture,source_type",
        [
            ("canonical_document.confluence.json", "confluence"),
            ("canonical_document.jira.json", "jira"),
            ("canonical_document.file.json", "file"),
        ],
    )
    def test_required_top_level_fields(self, fixture: str, source_type: str) -> None:
        doc = _load(fixture)
        assert doc["source_type"] == source_type
        assert doc["source_id"].startswith("SRC")
        assert len(doc["source_id"]) == len("SRC") + 32
        assert doc["content_hash"].startswith("sha256:")
        assert doc["metadata_hash"].startswith("sha256:")
        assert doc["body_format"] == "markdown"
        assert doc["body_markdown"].endswith("\n") or doc["body_markdown"] == ""
        assert "structure" in doc
        assert "profile_hints" in doc
        assert doc["profile_hints"]["profile_version"] == 1
        assert doc["extraction_status"] == "COMPLETE"

    def test_confluence_composition_and_markers(self) -> None:
        doc = _load("canonical_document.confluence.json")
        assert doc["body_markdown"].startswith(f"# {doc['title']}")
        assert "| ID | Statement |" in doc["body_markdown"]
        assert "|---|---|" in doc["body_markdown"]
        assert doc["hierarchy"]["section_path"]

    def test_jira_composition(self) -> None:
        doc = _load("canonical_document.jira.json")
        assert doc["body_markdown"].startswith(f"# {doc['metadata']['jira_key']}:")
        assert "## Description" in doc["body_markdown"]

    def test_file_page_markers(self) -> None:
        doc = _load("canonical_document.file.json")
        assert "--- Page 1 ---" in doc["body_markdown"]
        assert "--- Page 2 ---" in doc["body_markdown"]
        assert doc["structure"]["page_count"] == 2
        assert doc["raw_format"] == "pdf"

    def test_requirement_ids_present_in_confluence_and_file(self) -> None:
        conf = _load("canonical_document.confluence.json")
        assert "VAULT-TSR-100" in conf["structure"]["requirement_ids"]
        file_doc = _load("canonical_document.file.json")
        assert "VAULT-TSR-100" in file_doc["structure"]["requirement_ids"]
