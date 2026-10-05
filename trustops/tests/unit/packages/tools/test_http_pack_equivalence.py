"""The HTTP registry changes nothing a context-pack builder sees.

The demo case is served by scripted S1 and S3 peers whose answers come from the
seed (through the seed-backed tools, re-shaped the way the real services shape
them: S3's ``control_ratings`` and ``band``, S1's structured field sources). The
real builders from ``fcrm_agents`` then build the screening, intake and research
packs over the HTTP registry and over the seed-backed one, and the packs must be
identical: same ``pack_hash``, same sections, same citable sources, same tools
called. Where the two registries differ on purpose (a field's source is written
``<doc id> p.N l.M`` instead of the seed's ``case p.N l.M``) the seed-backed side
is given the same label, so a difference in the pack can only come from the
adaptation under test.

``policy_search`` and ``control_inventory`` have no HTTP backing yet; the research
pack is built over a registry that is explicitly the HTTP tools plus those two
seed-backed ones.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fcrm_agents import builder_for
from fcrm_agents.demo import demo_request
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.provenance import DocSourceRef
from fcrm_tools import ToolNotWiredError


async def _packs(
    demo_peers: SimpleNamespace, agent: AgentName, *, hybrid: bool = False
) -> tuple[object, object]:
    request = demo_request(agent)
    builder = builder_for(agent)
    oracle = await builder.build(request, demo_peers.oracle)
    over_http = await builder.build(
        request, demo_peers.hybrid if hybrid else demo_peers.http
    )
    return oracle, over_http


@pytest.mark.parametrize(
    ("agent", "hybrid"),
    [
        (AgentName.SCREENING, False),
        (AgentName.INTAKE, False),
        (AgentName.RESEARCH, True),
    ],
    ids=["screening", "intake", "research"],
)
async def test_the_pack_over_http_is_the_pack_over_the_seed(
    demo_peers: SimpleNamespace, agent: AgentName, hybrid: bool
) -> None:
    oracle, over_http = await _packs(demo_peers, agent, hybrid=hybrid)
    assert over_http.sections == oracle.sections  # type: ignore[attr-defined]
    assert over_http.pack_hash == oracle.pack_hash  # type: ignore[attr-defined]
    assert over_http.tools_called == oracle.tools_called  # type: ignore[attr-defined]
    assert [s.model_dump() for s in over_http.sources] == [  # type: ignore[attr-defined]
        s.model_dump()
        for s in oracle.sources  # type: ignore[attr-defined]
    ]
    assert over_http.token_estimate == oracle.token_estimate  # type: ignore[attr-defined]


async def test_the_screening_pack_shows_the_adapted_precedent_data(
    demo_peers: SimpleNamespace,
) -> None:
    _, pack = await _packs(demo_peers, AgentName.SCREENING)
    precedents = pack.sections["precedents"]  # type: ignore[attr-defined]
    assert "FCRM-2026-" in precedents
    fields = pack.sections["fields"]  # type: ignore[attr-defined]
    assert "F20 · " in fields
    # A document-sourced field names the document, page and line it came from.
    assert "business_case_remittance_MX.pdf p.4 l.12" in fields


async def test_the_research_pack_reads_the_adapted_ratings_and_dates(
    demo_peers: SimpleNamespace,
) -> None:
    _, pack = await _packs(demo_peers, AgentName.RESEARCH, hybrid=True)
    precedent_lines = pack.sections["precedents"].splitlines()  # type: ignore[attr-defined]
    assert precedent_lines
    # id · title · types · outcome · closed_date · score · ratings CTL-x r, ...
    assert all(" · ratings CTL-" in line for line in precedent_lines)
    assert all(line.split(" · ")[4].startswith("20") for line in precedent_lines)


async def test_the_intake_pack_cites_the_pages_it_shows(
    demo_peers: SimpleNamespace,
) -> None:
    _, pack = await _packs(demo_peers, AgentName.INTAKE)
    cited = [
        s.source_ref
        for s in pack.sources  # type: ignore[attr-defined]
        if isinstance(s.source_ref, DocSourceRef)
    ]
    assert cited
    assert {ref.doc_id for ref in cited} == {"business_case_remittance_MX.pdf"}
    assert [ref.page for ref in cited] == sorted(ref.page for ref in cited)


async def test_a_pack_whose_tool_has_no_http_backing_fails_loudly(
    demo_peers: SimpleNamespace,
) -> None:
    """Research over the pure HTTP registry cannot silently use seed data."""
    with pytest.raises(ToolNotWiredError, match="policy_search"):
        await builder_for(AgentName.RESEARCH).build(
            demo_request(AgentName.RESEARCH), demo_peers.http
        )


async def test_the_http_pack_calls_the_peers_and_the_object_store(
    demo_peers: SimpleNamespace,
) -> None:
    await builder_for(AgentName.INTAKE).build(
        demo_request(AgentName.INTAKE), demo_peers.http
    )
    paths = {r.url.path for r in demo_peers.peers.requests}
    assert "/internal/cases/FCRM-2026-0151/record" in paths
    assert "/field-sets/v6/resolve" in paths
    assert "/internal/documents/business_case_remittance_MX.pdf" in paths
