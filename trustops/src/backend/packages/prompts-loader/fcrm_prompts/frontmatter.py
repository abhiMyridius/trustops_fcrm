"""YAML front-matter splitting for ``*.md`` prompt and skill files (design B
§1.4, §1.5): a first line ``---``, a YAML mapping, a closing ``---``, then
the body. The body is returned byte-for-byte (no stripping) so the
template sha256 in the lock is the sha256 of exactly what is rendered.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from fcrm_prompts.errors import FrontMatterError

logger = logging.getLogger(__name__)

_DELIMITER = "---"


def split_front_matter(
    text: str, *, source: str = "<string>"
) -> tuple[dict[str, Any], str]:
    """Split ``text`` into its front-matter mapping and its body.

    Raises:
        FrontMatterError: no opening delimiter, no closing delimiter, invalid
            YAML, or YAML that is not a mapping.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _DELIMITER:
        msg = f"{source}: missing front-matter (first line must be '---')"
        raise FrontMatterError(msg)
    for index in range(1, len(lines)):
        if lines[index].strip() == _DELIMITER:
            raw = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return _parse_mapping(raw, source=source), body
    msg = f"{source}: front-matter is not terminated by '---'"
    raise FrontMatterError(msg)


def _parse_mapping(raw: str, *, source: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.error("front-matter YAML error", extra={"source": source})
        msg = f"{source}: front-matter is not valid YAML ({exc})"
        raise FrontMatterError(msg) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{source}: front-matter must be a mapping, got {type(data).__name__}"
        raise FrontMatterError(msg)
    return data
