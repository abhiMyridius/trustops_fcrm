"""The prompt-injection pattern list Guard 1 scores text against.

Data only, so the list is easy to review and extend: add a row, give it a
stable ``pattern_id`` (it appears in audit records) and a weight. Design B
§4.2 names the families: "ignore previous", "you are now", "system:",
tool-call look-alikes, base64 blobs > 200 chars, zero-width characters,
HTML comments and ``<|`` tokens. Two domain-specific families are added for
this workbench (instructions aimed at the score/band, and at the decision),
because a business case that tells the model to "rate this control 9" is
exactly the attack a risk tool invites.

Scoring (see ``input_scan.injection_score``): the weights of the *distinct*
patterns that match are summed and capped at 1.0. A single strong marker
(weight >= ``INJECTION_SCORE_BLOCK``) blocks on its own; weaker markers only
block in combination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from fcrm_guardrails.thresholds import BASE64_BLOB_MIN_CHARS, REPEATED_TOKEN_MIN_RUN


@dataclass(frozen=True)
class InjectionPattern:
    """One scored injection marker."""

    pattern_id: str
    regex: re.Pattern[str]
    weight: float
    description: str


def _p(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    """Compile with case-insensitive matching by default."""
    return re.compile(pattern, flags)


INJECTION_PATTERNS: Final[tuple[InjectionPattern, ...]] = (
    InjectionPattern(
        "ignore_previous",
        _p(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?"
            r"\b(?:previous|prior|above|earlier|preceding|all|any|your)\b[^.\n]{0,30}?"
            r"\b(?:instructions?|prompts?|rules|directions|guidelines|guardrails)\b"
        ),
        0.8,
        "classic instruction override",
    ),
    InjectionPattern(
        "role_reassignment",
        _p(
            r"\byou are now\b|\bfrom now on,? you\b|\bpretend (?:to be|you are)\b"
            r"|\bact as (?:an?|the|my)\b|\brole-?play as\b"
        ),
        0.5,
        "role-play / persona reassignment",
    ),
    InjectionPattern(
        "jailbreak_marker",
        _p(r"\bjailbreak\b|\bdo anything now\b|\bdeveloper mode\b|\bDAN mode\b"),
        0.6,
        "named jailbreak framings",
    ),
    InjectionPattern(
        "system_prompt_probe",
        _p(
            r"\bsystem prompt\b|\b(?:reveal|print|show|repeat)\b[^.\n]{0,20}"
            r"\b(?:your|the)\b[^.\n]{0,10}\b(?:prompt|instructions)\b"
        ),
        0.5,
        "attempts to read or replace the system prompt",
    ),
    InjectionPattern(
        "chat_role_marker",
        _p(r"^\s*(?:system|assistant|developer)\s*:", re.IGNORECASE | re.MULTILINE),
        0.4,
        "a line pretending to be a chat-role turn",
    ),
    InjectionPattern(
        "special_tokens",
        _p(r"<\|[a-z_]{2,30}\|>|\[/?INST\]|<</?SYS>>|<\|"),
        0.6,
        "model special tokens (<|...|>, [INST], <<SYS>>)",
    ),
    InjectionPattern(
        "tool_call_lookalike",
        _p(
            r"</?(?:tool_call|function_call|tool_use|tool_result|invoke)\b"
            r"|\"(?:tool|function)_?call\"\s*:|\"tool_name\"\s*:"
        ),
        0.6,
        "text shaped like a tool call or tool result",
    ),
    InjectionPattern(
        "score_manipulation",
        _p(
            r"\b(?:set|change|make|rate|mark|give)\b[^.\n]{0,40}?"
            r"\b(?:score|band|rating|risk level)\b[^.\n]{0,25}?\b(?:to|as|of)\b"
            r"|\b(?:rate|score|set)\b[^.\n]{0,40}?"
            r"\b(?:control|ctl-\w+|due diligence)\b[^.\n]{0,20}?\b(?:to|as|at)\s+\d"
        ),
        0.4,
        "instructions aimed at the score, band or a rating",
    ),
    InjectionPattern(
        "decision_manipulation",
        _p(
            r"\b(?:you must|you should|always|please)\b[^.\n]{0,20}"
            r"\b(?:approve|auto-?approve|fast[- ]lane)\b[^.\n]{0,20}"
            r"\b(?:this|the)\b[^.\n]{0,10}\b(?:case|change|request|submission)\b"
        ),
        0.4,
        "instructions aimed at the outcome or the fast lane",
    ),
    InjectionPattern(
        "exfiltration",
        _p(
            r"\b(?:send|post|upload|exfiltrate|forward)\b[^.\n]{0,40}"
            r"(?:https?://|\bapi[ _-]?keys?\b|\bcredentials\b|\bpasswords?\b)"
        ),
        0.5,
        "instructions to move data or secrets out",
    ),
    InjectionPattern(
        "html_comment",
        _p(r"<!--.*?-->", re.IGNORECASE | re.DOTALL),
        0.2,
        "hidden HTML comment",
    ),
    InjectionPattern(
        "zero_width",
        re.compile("[​‌‍⁠﻿]"),
        0.3,
        "zero-width characters (hidden text)",
    ),
    InjectionPattern(
        "base64_blob",
        re.compile(rf"[A-Za-z0-9+/]{{{BASE64_BLOB_MIN_CHARS},}}={{0,2}}"),
        0.3,
        f"base64-looking run of {BASE64_BLOB_MIN_CHARS}+ characters",
    ),
    InjectionPattern(
        "repeated_token_flood",
        _p(rf"\b(\w+)\b(?:\W+\1\b){{{REPEATED_TOKEN_MIN_RUN - 1},}}"),
        0.3,
        f"the same word {REPEATED_TOKEN_MIN_RUN}+ times in a row",
    ),
)
