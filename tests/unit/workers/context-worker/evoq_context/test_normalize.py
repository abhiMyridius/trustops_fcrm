"""Tests for evoq_context/chunking/normalize.py."""

from __future__ import annotations

from evoq_context.chunking.normalize import normalize_body


def test_json_envelope_is_unwrapped() -> None:
    body = '{"status":"success","content":"# Title\\n\\nReal content here."}'
    text, info = normalize_body(body, source_type="file")
    assert text == "# Title\n\nReal content here."
    assert info["envelope_unwrapped"] is True


def test_non_envelope_json_is_left_alone() -> None:
    body = '{"foo": "bar"}'
    text, info = normalize_body(body, source_type="file")
    assert text == body
    assert info["envelope_unwrapped"] is False


def test_malformed_json_envelope_does_not_raise() -> None:
    body = '{"status":"success", not valid json'
    text, info = normalize_body(body, source_type="file")
    assert text == body
    assert info["envelope_unwrapped"] is False


def test_canonical_markdown_passes_through_unchanged() -> None:
    body = "# Heading\n\nSome **bold** text.\n\n## Sub\n\nMore text."
    text, info = normalize_body(body, source_type="confluence")
    assert text == body
    assert info["html_converted"] is False
    assert info["envelope_unwrapped"] is False


def test_html_is_converted_to_markdown() -> None:
    html = "<h1>Title</h1><p>Para <strong>bold</strong> text.</p>"
    text, info = normalize_body(html, source_type="confluence")
    assert info["html_converted"] is True
    assert text.startswith("# Title")
    assert "**bold**" in text


def test_html_table_converts_to_gfm_table() -> None:
    html = (
        "<table><thead><tr><th>Req</th><th>Text</th></tr></thead>"
        "<tbody><tr><td>REQ-001</td><td>Must do X</td></tr></tbody></table>"
    )
    text, info = normalize_body(html, source_type="confluence")
    assert info["html_converted"] is True
    assert "| Req | Text |" in text
    assert "REQ-001" in text


def test_confluence_code_macro_survives_as_fenced_code() -> None:
    html = (
        "<h1>Example</h1>"
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        '<ac:plain-text-body><![CDATA[print("hi")]]></ac:plain-text-body>'
        "</ac:structured-macro>"
    )
    text, info = normalize_body(html, source_type="confluence")
    assert info["html_converted"] is True
    assert "```" in text
    assert "print" in text


def test_legacy_jira_comments_are_rerendered_from_raw_metadata() -> None:
    body = "PROJ-1\n\nSome legacy stringified body\n\nComments:\n{'author': 'Alice', 'body': 'hi'}"
    metadata = {
        "jira_key": "PROJ-1",
        "description": "Do the thing.",
        "comments": [
            {"author": "Alice", "body": "Looks good.", "created": "2026-01-01"},
            {"author": "Bob", "body": "Ship it.", "created": "2026-01-02"},
        ],
    }
    text, info = normalize_body(body, source_type="jira", metadata=metadata)
    assert info["jira_rerendered"] is True
    assert "# PROJ-1" in text
    assert "## Description" in text
    assert "Do the thing." in text
    assert "### Comment 1 — Alice · 2026-01-01" in text
    assert "### Comment 2 — Bob · 2026-01-02" in text
    assert "Looks good." in text
    assert "Ship it." in text


def test_canonical_jira_body_with_comment_markers_is_not_rerendered() -> None:
    body = (
        "# PROJ-1: Summary\n\n## Description\n\nDo the thing.\n\n"
        "## Comments (1)\n\n### Comment 1 — Alice · 2026-01-01\n\nLooks good.\n"
    )
    metadata = {
        "jira_key": "PROJ-1",
        "comments": [
            {"author": "Alice", "body": "Looks good.", "created": "2026-01-01"}
        ],
    }
    text, info = normalize_body(body, source_type="jira", metadata=metadata)
    assert info["jira_rerendered"] is False
    assert text == body


def test_jira_without_dict_comments_is_not_rerendered() -> None:
    body = "PROJ-1\n\nDescription text.\n\nComments:\nplain string comment"
    metadata = {"jira_key": "PROJ-1", "comments": ["plain string comment"]}
    text, info = normalize_body(body, source_type="jira", metadata=metadata)
    assert info["jira_rerendered"] is False
    assert text == body
