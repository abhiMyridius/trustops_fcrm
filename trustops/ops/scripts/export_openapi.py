#!/usr/bin/env python3
"""Export the api process's merged OpenAPI document (``make openapi``).

The composite ``fcrm_api`` mounts X1 and S1-S5 as sub-apps (ADR-26), so its
own ``app.openapi()`` lists only the aggregate health routes. This builds the
document a client actually sees: every mounted service's schema, with each
path prefixed by its mount prefix (``fcrm_api.registry.MOUNTS``), in one
file, ``docs/architecture/openapi/api.json``.

Merging rules:

* paths are prefixed with the mount prefix; the same public path from two
  mounts is an error (it would mean two services answer one URL);
* ``operationId`` is prefixed with the component id so ids stay unique (every
  FastAPI app has its own ``healthz_healthz_get``);
* schema names that two components define differently (``HTTPValidationError``
  is identical everywhere, a service model of the same name is not) are
  renamed ``<component>_<Name>`` everywhere they appear, ``$ref`` s included;
  identical definitions merge into one.

The output is deterministic (sorted keys, no timestamps), so the committed
file is a contract lock: ``--check`` and the unit test fail when it is stale.
The web's ``src/test/contract/paths.test.ts`` checks every route it calls
against it.

Usage::

    uv run --project src/backend --all-packages python ops/scripts/export_openapi.py
    ... export_openapi.py --check      # exit 1 if the committed file is stale
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("export_openapi")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs" / "architecture" / "openapi" / "api.json"
SCHEMA_REF = "#/components/schemas/"
BASE_ID = "api"

Document = dict[str, Any]
#: (component id, public prefix, that service's OpenAPI document)
Mounted = tuple[str, str, Document]


def _rewrite_refs(node: Any, renames: dict[str, str]) -> Any:
    """``node`` with every ``#/components/schemas/<old>`` renamed per ``renames``."""
    if isinstance(node, dict):
        return {key: _rewrite_refs(value, renames) for key, value in node.items()}
    if isinstance(node, list):
        return [_rewrite_refs(item, renames) for item in node]
    if isinstance(node, str) and node.startswith(SCHEMA_REF):
        name = node[len(SCHEMA_REF) :]
        return SCHEMA_REF + renames.get(name, name)
    return node


def _colliding_schema_names(documents: Iterable[tuple[str, Document]]) -> set[str]:
    """Schema names defined differently by two or more documents."""
    seen: dict[str, str] = {}
    clashing: set[str] = set()
    for _component, doc in documents:
        for name, schema in doc.get("components", {}).get("schemas", {}).items():
            encoded = json.dumps(schema, sort_keys=True)
            if seen.setdefault(name, encoded) != encoded:
                clashing.add(name)
    return clashing


def _merge_component_section(
    target: dict[str, Any], section: str, items: dict[str, Any], origin: str
) -> None:
    """Add ``items`` to ``target[section]``; a differing same-named item is an error."""
    bucket = target.setdefault(section, {})
    for name, value in items.items():
        if name in bucket and bucket[name] != value:
            raise ValueError(
                f"components.{section}.{name} differs between documents "
                f"(second definition from {origin})"
            )
        bucket[name] = value


def _add_paths(merged: Document, component: str, prefix: str, doc: Document) -> None:
    """Copy ``doc``'s paths into ``merged`` under ``prefix`` with unique ids."""
    for path, item in doc.get("paths", {}).items():
        public = f"{prefix.rstrip('/')}{path}"
        if public in merged["paths"]:
            raise ValueError(f"{public} is served by two mounts")
        for operation in item.values():
            if isinstance(operation, dict) and "operationId" in operation:
                operation["operationId"] = f"{component}_{operation['operationId']}"
        merged["paths"][public] = item


def _add_components(
    merged: Document, component: str, doc: Document, renames: dict[str, str]
) -> None:
    """Copy ``doc``'s components into ``merged``, applying the schema renames."""
    for section, items in doc.get("components", {}).items():
        if section == "schemas":
            items = {renames.get(name, name): schema for name, schema in items.items()}
        _merge_component_section(merged["components"], section, items, component)


def _add_tags(merged: Document, doc: Document) -> None:
    """Append ``doc``'s tags that ``merged`` does not have yet."""
    known = {tag["name"] for tag in merged["tags"]}
    for tag in doc.get("tags", []):
        if tag["name"] not in known:
            known.add(tag["name"])
            merged["tags"].append(tag)


def merge_documents(base: Document, mounted: list[Mounted]) -> Document:
    """One OpenAPI document from the composite's own routes plus each mount.

    Args:
        base: The composite app's own document (its aggregate health routes).
        mounted: ``(component_id, prefix, document)`` per mounted service.

    Returns:
        The merged document (inputs are not modified).

    Raises:
        ValueError: Two mounts publish the same public path, or define a
            non-schema component of one name differently.
    """
    documents = [
        (BASE_ID, "", copy.deepcopy(base)),
        *((c, prefix, copy.deepcopy(d)) for c, prefix, d in mounted),
    ]
    clashing = _colliding_schema_names((c, d) for c, _prefix, d in documents)
    merged: Document = {
        "openapi": base.get("openapi", "3.1.0"),
        "info": {
            "title": "TrustOps API",
            "version": base.get("info", {}).get("version", "0.0.0"),
            "description": (
                "Every service of the api process under its public prefix "
                "(X1 /api/fcrm/auth, S1 /api/fcrm, S2 /scoring, S3 /policy, "
                "S4 /audit, S5 /decision)."
            ),
        },
        "paths": {},
        "components": {},
        "tags": [],
    }
    for component, prefix, doc in documents:
        renames = {name: f"{component}_{name}" for name in clashing}
        doc = _rewrite_refs(doc, renames)
        _add_paths(merged, component, prefix, doc)
        _add_components(merged, component, doc, renames)
        _add_tags(merged, doc)
    for empty in ("tags", "components"):
        if not merged[empty]:
            del merged[empty]
    return merged


def build_document() -> Document:
    """The merged document of the live composite app (nothing is started)."""
    from fcrm_api.main import create_app  # heavy: only when exporting
    from fcrm_api.registry import MOUNTS

    app = create_app()
    mounted = [
        (
            mount.component_id,
            mount.prefix,
            app.state.mounted[mount.component_id].openapi(),
        )
        for mount in MOUNTS
    ]
    return merge_documents(app.openapi(), mounted)


def render(document: Document) -> str:
    """The committed form: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    """Write (or with ``--check`` verify) the merged OpenAPI file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 instead of writing when the file is missing or stale",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    text = render(build_document())
    out: Path = args.out
    current = out.read_text(encoding="utf-8") if out.exists() else None
    if args.check:
        if current != text:
            logger.error("%s is stale: run `make openapi` and commit the result", out)
            return 1
        logger.info("%s is up to date", out)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    logger.info("wrote %s (%d paths)", out, len(json.loads(text)["paths"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
