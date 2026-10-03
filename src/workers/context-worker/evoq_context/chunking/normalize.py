"""Canonical-Markdown normalization chain for chunking inputs.

Scope & functionality
---------------------
``NormalizerChain`` runs an ordered list of independent ``Normalizer``
objects over a raw item body before chunking and reports exactly what
happened in a ``NormalizeResult`` (``text``, ``format_detected``,
``applied``, ``failed``). The three defaults handle the legacy shapes still
present in already-ingested data and are no-ops for canonical ingestion
Markdown:

1. ``JsonEnvelopeNormalizer`` — ``{"status": "success", "content": "..."}``
   (or ``{"success": true, "content": ...}``) wrappers are unwrapped.
2. ``JiraCommentNormalizer`` — historic ``str(dict)`` comment bodies are
   rebuilt from raw ``metadata.comments`` in the Canonical Document
   ``### Comment {i} — {author} · {created}`` composition.
3. ``HtmlNormalizer`` — legacy Confluence HTML/XHTML → Markdown via
   ``markdownify`` (ATX headings, GFM tables) after a pre-pass that turns
   Confluence ``code`` macros into ``<pre><code>``. Detection scans the
   whole body **excluding fenced code**; a conversion failure is recorded
   (``failed=("html",)``, ``format_detected="html_raw"``) instead of
   silently chunking raw HTML as if it were Markdown.

``normalize_body`` is the thin legacy façade (``(text, info)`` tuple) kept
for the strategies module until the pipeline façade replaces it.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from evoq_context.chunking.markers import CODE_FENCE_RE, COMMENT_MARKER_RE
from evoq_context.core.models import JsonValue

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(
    r"<(h[1-6]|p|table|div|span|ul|ol|li|br|strong|em|a)\b[^<>]*>", re.IGNORECASE
)
_ENVELOPE_STATUS_KEYS = ("status", "success")


@dataclass(frozen=True, slots=True)
class NormalizeInput:
    """What a normalizer may look at besides the text itself."""

    source_type: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    """Outcome of the chain: the text to chunk and what was done to get it."""

    text: str
    format_detected: str = "markdown"
    applied: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()

    def to_info(self) -> dict[str, str | bool]:
        """Legacy ``info`` dict shape (``normalize_body`` callers)."""
        return {
            "envelope_unwrapped": "json_envelope" in self.applied,
            "html_converted": "html" in self.applied,
            "jira_rerendered": "jira_comments" in self.applied,
            "format_detected": self.format_detected,
            "failed": ",".join(self.failed),
        }


@runtime_checkable
class Normalizer(Protocol):
    """One independent normalization; returns the new text or ``None`` when not applicable."""

    @property
    def name(self) -> str: ...

    @property
    def detected_format(self) -> str | None: ...

    @property
    def failure_format(self) -> str | None: ...

    def normalize(self, text: str, inp: NormalizeInput) -> str | None: ...


class JsonEnvelopeNormalizer:
    """Unwrap ``{"status"|"success": ..., "content": "<real body>"}`` envelopes."""

    name = "json_envelope"
    detected_format: str | None = None
    failure_format: str | None = None

    def normalize(self, text: str, inp: NormalizeInput) -> str | None:
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return None
        try:
            data = json.loads(stripped)
        except ValueError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            return None
        if not any(key in data for key in _ENVELOPE_STATUS_KEYS):
            return None
        content: str = data["content"]
        return content


class JiraCommentNormalizer:
    """Rebuild a Jira body whose comments were stringified dicts, from raw metadata."""

    name = "jira_comments"
    detected_format: str | None = None
    failure_format: str | None = None

    def normalize(self, text: str, inp: NormalizeInput) -> str | None:
        if inp.source_type != "jira" or COMMENT_MARKER_RE.search(text):
            return None
        comments = inp.metadata.get("comments")
        if not isinstance(comments, list) or not comments:
            return None
        if not all(isinstance(c, dict) for c in comments):
            return None
        key = str(inp.metadata.get("jira_key") or "").strip()
        description = str(inp.metadata.get("description") or "").strip() or "_No description._"
        lines: list[str] = []
        if key:
            lines += [f"# {key}", ""]
        lines += ["## Description", "", description, "", f"## Comments ({len(comments)})", ""]
        for i, comment in enumerate(comments, start=1):
            assert isinstance(comment, dict)
            author = str(comment.get("author") or comment.get("author_name") or "Unknown").strip()
            created = str(comment.get("created") or "").strip()
            body = str(comment.get("body") or comment.get("text") or "").strip()
            lines += [f"### Comment {i} — {author} · {created}", "", body, ""]
        return "\n".join(lines).strip() + "\n"


class HtmlNormalizer:
    """Legacy HTML/XHTML → Markdown; failure is reported, never hidden."""

    name = "html"
    detected_format: str | None = "html"
    failure_format: str | None = "html_raw"

    def normalize(self, text: str, inp: NormalizeInput) -> str | None:
        if not self.looks_like_html(text):
            return None
        html = self._prerender_confluence_code_macros(text)
        converted: str = markdownify(
            html,
            heading_style="ATX",
            bullets="-",
            code_language_callback=self._code_language,
        )
        return converted.strip()

    @staticmethod
    def looks_like_html(text: str) -> bool:
        """Scan the whole body for block/inline HTML tags, skipping fenced code."""
        return bool(_HTML_TAG_RE.search(_without_fenced_code(text)))

    @staticmethod
    def _code_language(el: Tag) -> str:
        raw: str | list[str] = el.get("class") or []
        classes: list[str] = [raw] if isinstance(raw, str) else [str(c) for c in raw]
        for cls in classes:
            if cls.startswith("language-"):
                return cls[len("language-") :]
        return ""

    @staticmethod
    def _prerender_confluence_code_macros(html: str) -> str:
        """Turn ``<ac:structured-macro ac:name="code">`` into ``<pre><code>``."""
        soup = BeautifulSoup(html, "html.parser")
        for macro in soup.find_all("ac:structured-macro", attrs={"ac:name": "code"}):
            if not isinstance(macro, Tag):
                continue
            language = ""
            for param in macro.find_all("ac:parameter"):
                if isinstance(param, Tag) and param.get("ac:name") == "language":
                    language = (param.text or "").strip()
            body_tag = macro.find("ac:plain-text-body")
            code_text = body_tag.text if isinstance(body_tag, Tag) else macro.get_text()
            pre = soup.new_tag("pre")
            code = soup.new_tag("code")
            if language:
                code["class"] = f"language-{language}"
            code.string = code_text
            pre.append(code)
            macro.replace_with(pre)
        return str(soup)


def _without_fenced_code(text: str) -> str:
    kept: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


DEFAULT_NORMALIZERS: tuple[Normalizer, ...] = (
    JsonEnvelopeNormalizer(),
    JiraCommentNormalizer(),
    HtmlNormalizer(),
)


class NormalizerChain:
    """Run normalizers in order; each sees the previous one's output."""

    def __init__(self, normalizers: Sequence[Normalizer] = DEFAULT_NORMALIZERS) -> None:
        self._normalizers = tuple(normalizers)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n.name for n in self._normalizers)

    def run(
        self,
        text: str,
        *,
        source_type: str = "",
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> NormalizeResult:
        inp = NormalizeInput(source_type=source_type, metadata=metadata or {})
        current = text or ""
        applied: list[str] = []
        failed: list[str] = []
        format_detected = "markdown"
        for normalizer in self._normalizers:
            try:
                result = normalizer.normalize(current, inp)
            except Exception:
                logger.exception("normalizer %s failed; continuing with the unmodified text", normalizer.name)
                failed.append(normalizer.name)
                if normalizer.failure_format:
                    format_detected = normalizer.failure_format
                continue
            if result is None:
                continue
            current = result
            applied.append(normalizer.name)
            if normalizer.detected_format:
                format_detected = normalizer.detected_format
        return NormalizeResult(
            text=current,
            format_detected=format_detected,
            applied=tuple(applied),
            failed=tuple(failed),
        )


_DEFAULT_CHAIN = NormalizerChain()


def normalize_body(
    body: str,
    *,
    source_type: str = "",
    metadata: Mapping[str, JsonValue] | None = None,
) -> tuple[str, dict[str, str | bool]]:
    """Legacy façade over the default chain: ``(normalized_markdown, info)``."""
    result = _DEFAULT_CHAIN.run(body, source_type=source_type, metadata=metadata)
    return result.text, result.to_info()
