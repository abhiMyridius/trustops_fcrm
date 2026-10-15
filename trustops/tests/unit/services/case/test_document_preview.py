"""The source preview behind a citation chip (ADR-76): ``GET
/cases/{id}/documents/{doc_id}/pages`` and ``.../file`` against the real S1 app
on in-memory stores and a fake object store. Who may look (the owner and an
analyst), what they get (the anchored lines, the cited span, the upload inline),
and what happens when the text or the store is not there.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fcrm_case.document_preview import DocumentFile, parse_anchored
from fcrm_case.domain.views import DocumentRow
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_contracts.enums import Role
from fcrm_guardrails.rules.r3_po_no_restricted import check

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
PO = "u-pnair"
OTHER_PO = "u-someone-else"
ANALYST = "u-riyer"
RUN = "run_preview"
BUSINESS_CASE = "business_case_remittance_MX.pdf"
FIXTURES = Path(__file__).resolve().parents[4] / "evals/fixtures/documents"
CANONICAL = "canonical"

ANCHORED = """# doc (2 pages)
## Page 1
p.1 l.1: Business case
p.1 l.2: Owner: Priya Nair, Payments
p.1 l.3: Consumer remittances US to Mexico through payout partner Remesas del Norte S.A.
p.1 l.4: Launch window
p.1 l.5: Q1 2027
p.1 l.6: Volumes
p.1 l.7: USD 38M in year one
p.1 l.8: Channel
p.1 l.9: Mobile app and web
## Page 2
p.2 l.1: Annex A
p.2 l.2: The inherent risk of the corridor is discussed with the partner quarterly.
"""


@pytest.fixture
def ready(
    seeded_service: CaseService,
    run_h1_intake: Callable[..., None],
    act_as: Callable[[Role, str], None],
) -> CaseService:
    """The demo case with its documents uploaded, read by H3 (every document
    ``extracted``) and the business case's anchored text in the fake store.
    """
    act_as(Role.PO, PO)
    seeded_service.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    run_h1_intake(CASE, RUN)
    act_as(Role.PO, PO)
    row = business_case(seeded_service)
    assert row.anchored_key is not None
    seeded_service.objects.objects[(CANONICAL, row.anchored_key)] = (
        ANCHORED.encode("utf-8"),
        "text/markdown",
    )
    return seeded_service


def business_case(service: CaseService) -> DocumentRow:
    """The business case's document row."""
    return next(
        d
        for d in service.views.documents.values()
        if d.case_id == CASE and d.name == BUSINESS_CASE
    )


def doc_id(service: CaseService) -> str:
    return business_case(service).doc_id


# ── the parser and the file header ───────────────────────────────────────────


def test_parse_anchored_keeps_each_page_s_lines_in_order_and_skips_other_lines() -> (
    None
):
    pages = parse_anchored(
        "# x\n## Page 1\np.1 l.2: b\np.1 l.1: a\nnoise\np.3 l.1: c\n"
    )

    assert [
        (p, [(line.line, line.text) for line in lines]) for p, lines in pages.items()
    ] == [
        (1, [(1, "a"), (2, "b")]),
        (3, [(1, "c")]),
    ]


def test_the_file_is_sent_inline_with_an_ascii_and_a_utf8_name() -> None:
    file = DocumentFile(
        name='carta "DD" é.pdf', content_type="application/pdf", data=b""
    )

    assert file.content_disposition == (
        'inline; filename="carta DD .pdf"; '
        "filename*=UTF-8''carta%20%22DD%22%20%C3%A9.pdf"
    )


def test_a_name_with_nothing_ascii_falls_back_to_a_plain_word() -> None:
    assert DocumentFile(
        name="合同.pdf", content_type="application/pdf", data=b""
    ).content_disposition.startswith('inline; filename="document.pdf"; ')


# ── the page route ───────────────────────────────────────────────────────────


class TestPage:
    def test_the_owner_gets_the_cited_lines_in_context(
        self, ready: CaseService
    ) -> None:
        res = ready.client.get(
            f"/cases/{CASE}/documents/{doc_id(ready)}/pages",
            params={"page": 1, "line": 5, "context": 2},
        )

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["doc_id"] == doc_id(ready)
        assert body["name"] == BUSINESS_CASE
        assert (body["page"], body["pages"]) == (1, 14)
        assert body["highlight"] == {"line_start": 5, "line_end": 5}
        assert [line["line"] for line in body["lines"]] == [3, 4, 5, 6, 7]
        assert body["lines"][2]["text"] == "Q1 2027"

    def test_without_a_line_the_whole_page_comes_back_unmarked(
        self, ready: CaseService
    ) -> None:
        body = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/pages").json()

        assert body["page"] == 1
        assert body["highlight"] is None
        assert len(body["lines"]) == 9

    def test_a_span_is_marked_from_line_to_line_end(self, ready: CaseService) -> None:
        body = ready.client.get(
            f"/cases/{CASE}/documents/{doc_id(ready)}/pages",
            params={"page": 1, "line": 2, "line_end": 3},
        ).json()

        assert body["highlight"] == {"line_start": 2, "line_end": 3}

    def test_the_owner_s_own_words_are_not_withheld(self, ready: CaseService) -> None:
        """The demo's business case says "inherent risk" on its own pages; the
        owner wrote it, so the preview shows it. The shape is still fence-clean:
        no key of the response is on the restricted list.
        """
        res = ready.client.get(
            f"/cases/{CASE}/documents/{doc_id(ready)}/pages", params={"page": 2}
        )

        assert res.status_code == 200, res.text
        assert "inherent risk" in res.json()["lines"][1]["text"]
        keys_only = {k: (None if k == "lines" else v) for k, v in res.json().items()}
        assert check(keys_only, Role.PO).ok

    def test_an_analyst_may_read_it_too(
        self, ready: CaseService, act_as: Callable[[Role, str], None]
    ) -> None:
        act_as(Role.ANALYST, ANALYST)

        res = ready.client.get(
            f"/cases/{CASE}/documents/{doc_id(ready)}/pages", params={"page": 2}
        )

        assert res.status_code == 200, res.text

    def test_another_owner_gets_404_not_403(
        self, ready: CaseService, act_as: Callable[[Role, str], None]
    ) -> None:
        act_as(Role.PO, OTHER_PO)

        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/pages")

        assert res.status_code == 404, res.text

    def test_a_committee_member_is_refused(
        self, ready: CaseService, act_as: Callable[[Role, str], None]
    ) -> None:
        act_as(Role.COMMITTEE, "u-amendes")

        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/pages")

        assert res.status_code == 403, res.text

    @pytest.mark.parametrize("params", [{"page": 15}, {"page": 0}])
    def test_a_page_outside_the_document_is_404_or_422(
        self, ready: CaseService, params: dict[str, int]
    ) -> None:
        res = ready.client.get(
            f"/cases/{CASE}/documents/{doc_id(ready)}/pages", params=params
        )

        assert res.status_code == (404 if params["page"] > 1 else 422), res.text

    def test_context_without_a_line_and_a_reversed_span_are_422(
        self, ready: CaseService
    ) -> None:
        path = f"/cases/{CASE}/documents/{doc_id(ready)}/pages"

        assert ready.client.get(path, params={"context": 3}).status_code == 422
        assert (
            ready.client.get(path, params={"line": 5, "line_end": 2}).status_code == 422
        )

    def test_a_document_of_another_case_or_none_at_all_is_404(
        self, ready: CaseService
    ) -> None:
        assert (
            ready.client.get(
                f"/cases/FCRM-2026-0150/documents/{doc_id(ready)}/pages"
            ).status_code
            == 404
        )
        assert (
            ready.client.get(f"/cases/{CASE}/documents/doc_nope/pages").status_code
            == 404
        )

    def test_a_document_h3_has_not_read_has_no_text_yet(
        self, seeded_service: CaseService, act_as: Callable[[Role, str], None]
    ) -> None:
        act_as(Role.PO, PO)
        seeded_service.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})

        res = seeded_service.client.get(
            f"/cases/{CASE}/documents/{doc_id(seeded_service)}/pages"
        )

        assert res.status_code == 404, res.text
        assert "no extracted text" in res.json()["detail"]

    def test_a_missing_anchored_object_is_404(self, ready: CaseService) -> None:
        row = business_case(ready)
        del ready.objects.objects[(CANONICAL, str(row.anchored_key))]

        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/pages")

        assert res.status_code == 404, res.text
        assert "missing" in res.json()["detail"]

    def test_an_unreachable_object_store_is_503(
        self, ready: CaseService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def down(bucket: str, key: str) -> bytes:
            raise UpstreamUnavailableError("The object store is down.", dependency="s3")

        monkeypatch.setattr(ready.objects, "get_bytes", down)

        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/pages")

        assert res.status_code == 503, res.text

    def test_reading_is_not_a_state_change(self, ready: CaseService) -> None:
        before = len(ready.store.events)

        ready.client.get(
            f"/cases/{CASE}/documents/{doc_id(ready)}/pages", params={"page": 2}
        )
        ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/file")

        assert len(ready.store.events) == before


# ── the file route ───────────────────────────────────────────────────────────


class TestFile:
    def test_the_owner_gets_the_upload_inline(self, ready: CaseService) -> None:
        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/file")

        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/pdf"
        assert res.headers["content-disposition"].startswith(
            f'inline; filename="{BUSINESS_CASE}"'
        )
        assert res.headers["cache-control"] == "private, no-store"
        assert res.content == (FIXTURES / BUSINESS_CASE).read_bytes()

    def test_an_analyst_gets_it_and_a_stranger_does_not(
        self, ready: CaseService, act_as: Callable[[Role, str], None]
    ) -> None:
        act_as(Role.ANALYST, ANALYST)
        assert (
            ready.client.get(
                f"/cases/{CASE}/documents/{doc_id(ready)}/file"
            ).status_code
            == 200
        )

        act_as(Role.PO, OTHER_PO)
        assert (
            ready.client.get(
                f"/cases/{CASE}/documents/{doc_id(ready)}/file"
            ).status_code
            == 404
        )

    def test_a_document_whose_object_is_gone_is_404(self, ready: CaseService) -> None:
        row = business_case(ready)
        del ready.objects.objects[(row.bucket, row.key)]

        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/file")

        assert res.status_code == 404, res.text

    def test_an_unreachable_object_store_is_503(
        self, ready: CaseService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def down(bucket: str, key: str) -> bytes:
            raise UpstreamUnavailableError("The object store is down.", dependency="s3")

        monkeypatch.setattr(ready.objects, "get_bytes", down)

        res = ready.client.get(f"/cases/{CASE}/documents/{doc_id(ready)}/file")

        assert res.status_code == 503, res.text
