"""The sandboxed Jinja2 environment prompts render in (design B §1.5).

Prompt templates are reviewed files, but they are rendered with case data
and are edited by more people (and agents) than application code, so they
are treated as untrusted-ish: rendering uses
:class:`jinja2.sandbox.ImmutableSandboxedEnvironment` (the sandbox that also
refuses in-place mutation of lists and dicts, a strict subclass of
``SandboxedEnvironment``), with:

- ``StrictUndefined``: an unknown variable is an error, never an empty string;
- autoescape off (prompts are plain text, not HTML);
- **every default global removed** (``range``, ``dict``, ``lipsum``,
  ``cycler``, ``joiner``, ``namespace``) and exactly four functions added,
  the four design B allows:

  ``skill(name)``
      inlines ``ai/skills/<name>.skill.md`` (body only, no front-matter).
  ``schema(model_path)``
      a compact JSON-Schema summary of a Pydantic model given as
      ``"module.ClassName"`` (or a bare class name resolved against the
      configured contract modules); only modules under the allowed prefixes
      (default ``fcrm_contracts``) may be imported.
  ``section(context_pack, key)`` / ``section(name, items, cap=N)``
      one named section of a context pack, or (design B's form, used by the
      ai/ lane's prompts) a list of items rendered under a token cap.
  ``config(key)``
      a value from ``ai/guards/thresholds.yaml`` or ``ai/models/tiers.yaml``
      by dotted key.

Attribute access to anything starting with an underscore (``__class__``,
``__globals__``, ``__subclasses__``, ``__self__`` ...) raises
:class:`jinja2.exceptions.SecurityError` inside the sandbox; the loader wraps
it in :class:`~fcrm_prompts.errors.TemplateRenderError`.
"""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import re
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any, Final, Optional

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import BaseModel

from fcrm_prompts.errors import (
    ConfigKeyError,
    SchemaResolutionError,
    SectionNotFoundError,
)

logger = logging.getLogger(__name__)

TEMPLATE_FUNCTIONS: Final[tuple[str, ...]] = ("skill", "schema", "section", "config")
"""The only callables a template can reach (design B §1.5)."""

DEFAULT_SCHEMA_PREFIXES: Final[tuple[str, ...]] = ("fcrm_contracts",)
"""Modules ``schema()`` may import. Contracts only: output schemas live in
``src/backend/packages/contracts`` (design B §1.9)."""

DEFAULT_SCHEMA_SEARCH: Final[tuple[str, ...]] = ("fcrm_contracts.proposals",)
"""Packages a bare class name (``schema("ClauseProposal")``) is searched in."""

_MODEL_PATH: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def build_environment(globals_: Mapping[str, Any]) -> ImmutableSandboxedEnvironment:
    """A fresh sandbox with no default globals and exactly ``globals_``."""
    env = ImmutableSandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.globals.clear()
    env.globals.update(globals_)
    return env


def _module_allowed(module_name: str, prefixes: Sequence[str]) -> bool:
    return any(module_name == p or module_name.startswith(f"{p}.") for p in prefixes)


def _import(module_name: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        logger.error("schema module import failed", extra={"module": module_name})
        msg = f"cannot import {module_name!r} ({exc})"
        raise SchemaResolutionError(msg) from exc


def _model_in(module: ModuleType, name: str) -> Optional[type[BaseModel]]:
    candidate = getattr(module, name, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


def _search_bare_name(name: str, search: Sequence[str]) -> type[BaseModel]:
    found: dict[str, type[BaseModel]] = {}
    for package_name in search:
        package = _import(package_name)
        module_names = [package_name]
        if hasattr(package, "__path__"):
            module_names += [
                f"{package_name}.{info.name}"
                for info in pkgutil.iter_modules(package.__path__)
            ]
        for module_name in module_names:
            model = _model_in(_import(module_name), name)
            if model is not None:
                found[f"{model.__module__}.{model.__qualname__}"] = model
    if len(found) != 1:
        where = ", ".join(sorted(found)) or "nowhere"
        msg = f"schema name {name!r} must name exactly one model; found {where}"
        raise SchemaResolutionError(msg)
    return next(iter(found.values()))


def resolve_model(
    model_path: str,
    *,
    prefixes: Sequence[str] = DEFAULT_SCHEMA_PREFIXES,
    search: Sequence[str] = DEFAULT_SCHEMA_SEARCH,
) -> type[BaseModel]:
    """The Pydantic model ``model_path`` names.

    Raises:
        SchemaResolutionError: malformed path, a module outside ``prefixes``,
            an import failure, or a name that is not a ``BaseModel``.
    """
    if not _MODEL_PATH.match(model_path):
        msg = f"malformed schema path {model_path!r}"
        raise SchemaResolutionError(msg)
    if "." not in model_path:
        return _search_bare_name(model_path, search)
    module_name, _, class_name = model_path.rpartition(".")
    if not _module_allowed(module_name, prefixes):
        msg = f"schema module {module_name!r} is outside {list(prefixes)}"
        raise SchemaResolutionError(msg)
    model = _model_in(_import(module_name), class_name)
    if model is None:
        msg = f"{model_path!r} is not a Pydantic model"
        raise SchemaResolutionError(msg)
    return model


def schema_summary(model: type[BaseModel]) -> str:
    """Compact JSON Schema of ``model``: sorted keys, no whitespace, field
    descriptions kept, ``title`` keys dropped (they repeat the field name and
    cost tokens on every call)."""
    return json.dumps(
        _drop_titles(model.model_json_schema()),
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    )


def _drop_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _drop_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_drop_titles(v) for v in node]
    return node


CHARS_PER_TOKEN: Final[int] = 4
"""(proposed) Character budget per token when ``section(name, items, cap)``
converts its token cap into a character cut."""


def _pack_section(context_pack: Any, key: str) -> str:
    sections: Any = context_pack
    if isinstance(context_pack, Mapping) and isinstance(
        context_pack.get("sections"), Mapping
    ):
        sections = context_pack["sections"]
    elif not isinstance(context_pack, Mapping):
        sections = getattr(context_pack, "sections", None)
    if not isinstance(sections, Mapping) or key not in sections:
        msg = f"context pack has no section {key!r}"
        raise SectionNotFoundError(msg)
    return str(sections[key])


def _item_line(item: Any) -> str:
    if isinstance(item, Mapping):
        return " · ".join(str(v) for v in item.values())
    if isinstance(item, (list, tuple)):
        return " · ".join(str(v) for v in item)
    return str(item)


def _render_items(items: Any, cap: Optional[int]) -> str:
    if isinstance(items, str):
        lines = items.splitlines()
    elif isinstance(items, Mapping):
        lines = [f"{k} · {_item_line(v)}" for k, v in items.items()]
    else:
        lines = [_item_line(item) for item in items]
    if cap is None:
        return "\n".join(lines)
    budget = cap * CHARS_PER_TOKEN
    shown: list[str] = []
    for line in lines:
        if sum(len(x) + 1 for x in shown) + len(line) > budget:
            break
        shown.append(line)
    if len(shown) < len(lines):
        omitted = len(lines) - len(shown)
        shown.append(f"[showing {len(shown)} of {len(lines)}; {omitted} omitted]")
    return "\n".join(shown)


def section_of(source: Any, key: Any, cap: Optional[int] = None) -> str:
    """``section`` in its two call forms.

    - ``section(context_pack, "key")``: one named section of a context pack
      (a mapping with a ``sections`` mapping, an object with a ``sections``
      attribute, or a plain mapping).
    - ``section("name", items, cap=N)`` (design B §1.5, the form the ai/
      lane's prompts use): ``items`` (text, a mapping or a list) one per
      line, cut at whole lines to ``N`` tokens with a visible
      ``[showing k of n; m omitted]`` marker, never silently.

    Raises:
        SectionNotFoundError: the pack form named a section the pack lacks.
    """
    if isinstance(source, str):
        return _render_items(key, cap)
    return _pack_section(source, str(key))


def lookup_dotted(data: Mapping[str, Any], key: str) -> tuple[bool, Any]:
    """``(found, value)`` for a dotted ``key`` walked through nested mappings."""
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def render_config_value(value: Any) -> str:
    """Scalars as ``str``; mappings and lists as compact flow-style YAML so
    the rendered text is stable byte-for-byte across renders."""
    if isinstance(value, (Mapping, list)):
        dumped: str = yaml.safe_dump(
            value, default_flow_style=True, sort_keys=True, width=10_000
        )
        return dumped.strip()
    return str(value)


def make_config_function(
    sources: Mapping[str, Callable[[], Mapping[str, Any]]],
) -> Callable[[str], str]:
    """``config(key)``: search each named source for ``key``.

    Raises (from the returned function):
        ConfigKeyError: no source holds ``key``, or more than one does.
    """

    def config(key: str) -> str:
        hits = []
        for name, load in sources.items():
            found, value = lookup_dotted(load(), key)
            if found:
                hits.append((name, value))
        if not hits:
            msg = f"config key {key!r} not in {list(sources)}"
            raise ConfigKeyError(msg)
        if len(hits) > 1:
            msg = f"config key {key!r} is ambiguous: in {[h[0] for h in hits]}"
            raise ConfigKeyError(msg)
        return render_config_value(hits[0][1])

    return config
