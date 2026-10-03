"""Fixtures for fcrm_context_worker tests (beside the suite until the lead
hoists them into tests/conftest.py; see the ingestion-worker conftest)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.seedload import seeded_store
from fcrm_seed.loader import load_raw


def _seed_corpus_markdown() -> str:
    """The seed's corpus v14 as corpus-shaped Markdown (design A §6.1).

    One ``##`` section per source (title, use, sections); each of the four
    seeded clauses under its source: public sources by clause-id heading
    (``### FFIEC-TPPP-04 …``), internal standards by the ``§`` number in the
    clause's own ``ref`` (``§3.1 …``, as a bare line). All text is the seed's.
    """
    corpus = load_raw()["corpus"]
    clauses = load_raw()["demo"]["clauses"]
    out = [f"# Policy corpus {corpus['version']}", ""]
    for source in corpus["sources"]:
        out += [f"## {source['title']}", "", source["used_for"], ""]
        out += [f"- {name}" for name in source["sections"]] + [""]
        for clause in (c for c in clauses if c["src"] == source["id"]):
            section = re.search(r"§\s?\d+(?:\.\d+)*", clause["ref"])
            if source["kind"] == "synthetic" and section:
                out.append(f"{section.group(0)} {clause['title']}")
            else:
                out.append(f"### {clause['clause_id']} {clause['title']}")
            out += ["", clause["quote"], "", f"Ref: {clause['ref']}", ""]
    return "\n".join(out)


@pytest.fixture(scope="module")
def corpus_md() -> str:
    """Seed corpus text shaped like a policy source document."""
    return _seed_corpus_markdown()


@pytest.fixture
async def seeded(tmp_path: Path) -> tuple[Any, Any, Any]:
    """(store, llm, config): the memory backend loaded with the seed clauses."""
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store, llm = await seeded_store(config)
    return store, llm, config
