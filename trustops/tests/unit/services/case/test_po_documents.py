"""``fcrm_case.po_documents``: what the PO's upload accepts (file name, type, size,
content) and where the demo documents come from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_case.po_documents import (
    DEMO_INTAKE_FILES,
    DirectoryFixtures,
    ObjectFixtures,
    check_file,
    clean_file_name,
    extension_of,
    read_limited,
)
from fcrm_case.po_errors import FileRefusedError, PayloadTooLargeError
from fcrm_common.errors import NotFoundError

FIXTURES = Path(__file__).resolve().parents[4] / "evals/fixtures/documents"
ALLOWED = [".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".md", ".txt"]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("plan.pdf", "plan.pdf"),
        ("  plan.pdf  ", "plan.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\win\\notes.txt", "notes.txt"),
        ("a/b/c.pdf", "c.pdf"),
        ("My plan (v2).txt", "My plan _v2_.txt"),
        ("tab\tand   spaces.csv", "tab_and spaces.csv"),
        ("naïve café.md", "na_ve caf_.md"),
        ("trailing dots....pdf", "trailing dots....pdf"),
    ],
)
def test_a_file_name_is_reduced_to_a_safe_base_name(raw: str, clean: str) -> None:
    assert clean_file_name(raw) == clean


def test_a_long_name_keeps_its_extension() -> None:
    cleaned = clean_file_name("x" * 300 + ".pdf")
    assert len(cleaned) <= 120
    assert cleaned.endswith(".pdf")


@pytest.mark.parametrize(
    "raw", ["", "   ", ".hidden", "noextension", "dir/", "..", "a."]
)
def test_a_name_with_nothing_usable_is_refused(raw: str) -> None:
    with pytest.raises(FileRefusedError):
        clean_file_name(raw)


def test_the_extension_is_lower_cased() -> None:
    assert extension_of("PLAN.PDF") == ".pdf"


@pytest.mark.parametrize(
    ("name", "data", "content_type"),
    [
        ("a.pdf", b"%PDF-1.7 ...", "application/pdf"),
        (
            "a.docx",
            b"PK\x03\x04rest",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "a.xlsx",
            b"PK\x03\x04rest",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("a.txt", "héllo".encode(), "text/plain"),
        ("a.csv", b"a,b\n1,2\n", "text/csv"),
        ("a.md", b"# title", "text/markdown"),
        ("A.PDF", b"%PDF-1.4", "application/pdf"),
    ],
)
def test_an_honest_file_is_accepted_with_the_content_type_its_extension_implies(
    name: str, data: bytes, content_type: str
) -> None:
    assert check_file(name, data, allowed=ALLOWED, max_bytes=1000) == content_type


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("a.exe", b"MZ", "only"),
        ("a.pdf", b"", "is empty"),
        ("a.pdf", b"<html>", "is not a PDF"),
        ("a.docx", b"%PDF-", "is not an Office document"),
        ("a.txt", b"a\x00b", "is not a text file"),
        ("a.txt", b"ok " * 10 + b"\xff\xfe\xfa" + b" more" * 10, "is not UTF-8 text"),
    ],
)
def test_a_file_that_is_not_what_it_claims_is_refused(
    name: str, data: bytes, reason: str
) -> None:
    with pytest.raises(FileRefusedError, match=reason):
        check_file(name, data, allowed=ALLOWED, max_bytes=1000)


def test_a_cut_multibyte_character_at_the_end_of_the_sample_is_not_a_refusal() -> None:
    data = ("é" * 5000).encode()  # 10000 bytes: the 8192-byte sample cuts one in two
    assert check_file("a.txt", data, allowed=ALLOWED, max_bytes=20000) == "text/plain"


def test_an_extension_off_the_allow_list_is_refused_even_when_known() -> None:
    with pytest.raises(FileRefusedError, match="only"):
        check_file("a.pdf", b"%PDF-1", allowed=[".txt"], max_bytes=1000)


def test_a_file_over_the_cap_is_too_large() -> None:
    with pytest.raises(PayloadTooLargeError):
        check_file("a.txt", b"x" * 1001, allowed=ALLOWED, max_bytes=1000)


class Chunks:
    """An uploaded file read in pieces."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.reads = 0

    async def read(self, size: int = -1, /) -> bytes:
        self.reads += 1
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk


async def test_a_file_is_read_in_chunks_up_to_the_cap() -> None:
    assert (
        await read_limited(Chunks(b"abc" * 10), name="a.txt", max_bytes=100)
        == b"abc" * 10
    )


async def test_reading_stops_the_moment_the_file_is_over_the_cap() -> None:
    upload = Chunks(b"x" * (3 * 1024 * 1024))
    with pytest.raises(PayloadTooLargeError):
        await read_limited(upload, name="big.pdf", max_bytes=1024 * 1024)
    assert upload.reads <= 3  # it did not read the whole file


async def test_the_directory_fixtures_read_the_committed_demo_pdfs() -> None:
    fixtures = DirectoryFixtures(FIXTURES)
    for name in DEMO_INTAKE_FILES:
        assert (await fixtures.read(name)).startswith(b"%PDF-")


async def test_a_name_that_is_not_a_fixture_is_not_found_and_cannot_climb_out() -> None:
    fixtures = DirectoryFixtures(FIXTURES)
    with pytest.raises(NotFoundError):
        await fixtures.read("nope.pdf")
    with pytest.raises(NotFoundError):
        await fixtures.read("../../../README.md")  # reduced to README.md


class Bucket:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        try:
            return self.objects[(bucket, key)]
        except KeyError as exc:
            raise NotFoundError("no object") from exc


async def test_the_object_fixtures_read_the_staged_copy() -> None:
    store = Bucket({("uploads", "_fixtures/a.pdf"): b"%PDF-staged"})
    fixtures = ObjectFixtures(store, "uploads")
    assert await fixtures.read("a.pdf") == b"%PDF-staged"
    with pytest.raises(NotFoundError, match="no demo document named b.pdf"):
        await fixtures.read("b.pdf")
