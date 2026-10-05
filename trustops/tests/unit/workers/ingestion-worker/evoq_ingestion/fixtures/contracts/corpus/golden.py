#!/usr/bin/env python
"""Golden harness for the shared AEO corpus (plan §3.3).

Scope & functionality
---------------------
Drives the two engines **as subprocesses** — never imports either one — over
the rows of ``scenarios.yaml`` (plus ``real/REAL-*.yaml`` sidecars):

* ingestion:  ``python -m evoq_ingestion extract --json <file> [engine_args]``  (cwd: aeoingestionjob/)
* context:    ``python -m evoq_context chunk --json <canonical.json | file.md>``   (cwd: aeocontextjob/)

and compares what comes back with (a) the per-row ``expected`` block and
(b) the pinned goldens in ``golden/<ID>.canonical.json`` / ``<ID>.chunks.json``.

Commands::

    golden.py pin            [--engine both] [--only 'PDF-*'] [--update-golden]   # write goldens
    golden.py verify         [--engine both] [--only 'PDF-*'] [--update-golden]   # expectations + golden diff
    golden.py report         [--out REPORT.md]                                    # verify + Markdown table
    golden.py export-vectors [--out ../vectors_fixture.json]                      # v2.1 records + parents
    golden.py run-e2e        [--report]                                            # corpus → ingestion → context → fixture

Exit codes: 0 ok · 1 expectation/golden failures · 2 usage · 3 an engine CLI is
not available yet (the engines are being (re)built by other lanes; every row
for that engine is reported as SKIPPED with the reason, nothing crashes).

Interpreter selection: ``--ingestion-python`` / ``--context-python`` default to
``<job>/.venv/bin/python`` when present, else ``sys.executable``.  The exact
CLI flags for non-file formats (Confluence HTML, Jira issue JSON) and for the
per-file deadline live in ``FORMAT_ARGS`` / the row's ``engine_args`` and are
reconciled with the engine lanes at L10 — until then those rows degrade to
SKIPPED with the engine's own error message.

Normalisation (what goes into a golden file): keys sorted, ``ensure_ascii=False``,
volatile fields dropped (timestamps, job ids, durations, absolute paths →
``<corpus>/…``), see ``VOLATILE_CANONICAL`` / ``VOLATILE_CHUNK``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML is required: pip install -r requirements-corpus.txt", file=sys.stderr)
    raise

CORPUS_ROOT = Path(__file__).resolve().parent
# TrustOps clone: this corpus lives under
# tests/unit/workers/ingestion-worker/evoq_ingestion/fixtures/contracts/corpus,
# and the engines under src/backend/workers/{ingestion,context}-worker (UPSTREAM.md).
REPO_ROOT = next(
    p
    for p in CORPUS_ROOT.parents
    if (p / "src" / "backend" / "pyproject.toml").exists()
)
SCENARIOS = CORPUS_ROOT / "scenarios.yaml"
GOLDEN_DIR = CORPUS_ROOT / "golden"
REAL_DIR = CORPUS_ROOT / "real"
WORK_DIR = CORPUS_ROOT / ".work"

ENGINE_CWD = {
    "ingestion": REPO_ROOT / "src" / "backend" / "workers" / "ingestion-worker",
    "context": REPO_ROOT / "src" / "backend" / "workers" / "context-worker",
}
ENGINE_MODULE = {"ingestion": "evoq_ingestion", "context": "evoq_context"}

# Extra CLI args per corpus `format` for the ingestion engine. Reconciled with L2g at L10.
FORMAT_ARGS: dict[str, list[str]] = {
    "confluence_storage_xhtml": ["--raw-format", "confluence_storage_xhtml"],
    "confluence_export_view_html": ["--raw-format", "confluence_export_view_html"],
    "jira_issue_json": ["--raw-format", "adf_json"],
}

REQUIREMENT_ID_PATTERN = r"\b([A-Z][A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12})*-\d{1,5})\b"
MARKER_RE = {
    "page": re.compile(r"^--- Page \d+ ---$", re.M),
    "sheet": re.compile(r"^--- Sheet: .+ ---$", re.M),
    "slide": re.compile(r"^--- Slide \d+ ---$", re.M),
    "comment": re.compile(r"^### Comment \d+ — .+ · .+$", re.M),
}

VOLATILE_CANONICAL = {
    "duration_ms",
    "job_id",
    "created_on",
    "created_by",
    "first_seen_at",
    "last_seen_at",
    "last_seen_job_id",
    "metadata_updated_at",
    "source_updated_at",
    "source_created_at",
    "source_version",
    "raw_path",
    "body_path",
    "file_path",
    "extracted_text_path",
    "superseded_at",
    "superseded_by_version",
    "removed_at_source",
    "ingested_at",
    "raw_content",
}
VOLATILE_CHUNK = {"ingested_at", "snapshot_version", "project", "source_version"}


# --------------------------------------------------------------------------- data model


@dataclass
class Row:
    id: str
    file: str
    format: str
    expected: dict[str, Any]
    on_demand: bool = False
    invoke: str = "file"
    engine_args: list[str] = field(default_factory=list)
    todo: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str = "scenarios.yaml"

    @property
    def path(self) -> Path:
        return CORPUS_ROOT / self.file

    @property
    def chunk_only(self) -> bool:
        return self.expected.get("canonical") is None

    @property
    def canonical_expected(self) -> dict[str, Any] | None:
        return self.expected.get("canonical")

    @property
    def chunking_expected(self) -> dict[str, Any] | None:
        return self.expected.get("chunking")


@dataclass
class Outcome:
    row_id: str
    engine: str  # ingestion | context
    status: str  # PASS | FAIL | SKIP
    notes: list[str] = field(default_factory=list)
    golden: str = "-"  # match | diff | pinned | missing | -


class EngineUnavailable(Exception):
    """Raised when `python -m <engine>` cannot even print its help."""


# --------------------------------------------------------------------------- loading


def load_rows(only: str | None = None) -> list[Row]:
    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    rows = [_row_from(d, "scenarios.yaml") for d in data["scenarios"]]
    for side in sorted(REAL_DIR.glob("REAL-*.yaml")):
        d = yaml.safe_load(side.read_text(encoding="utf-8")) or {}
        if "id" not in d or "file" not in d:
            continue
        if any(r.id == d["id"] for r in rows):
            continue
        if not _anonymisation_complete(d):
            print(
                f"[real] {d['id']}: anonymisation checklist incomplete in {side.name} — skipped"
            )
            continue
        rows.append(_row_from(d, side.name))
    if only:
        rows = [r for r in rows if fnmatch.fnmatch(r.id, only)]
    return rows


def _row_from(d: dict[str, Any], source: str) -> Row:
    return Row(
        id=d["id"],
        file=d["file"],
        format=d.get("format", ""),
        expected=d.get("expected") or {},
        on_demand=bool(d.get("on_demand")),
        invoke=d.get("invoke", "file"),
        engine_args=list(d.get("engine_args") or []),
        todo=d.get("todo"),
        tags=list(d.get("tags") or []),
        source=source,
    )


def _anonymisation_complete(d: dict[str, Any]) -> bool:
    items = d.get("anonymisation") or {}
    for key, val in items.items():
        if isinstance(val, dict) and val.get("done") is False:
            return False
    return True


# --------------------------------------------------------------------------- engines


class Engine:
    def __init__(self, name: str, python: str) -> None:
        self.name = name
        self.python = python
        self.cwd = ENGINE_CWD[name]
        self.module = ENGINE_MODULE[name]
        self._probe_error: str | None = None
        self._probed = False

    def probe(self) -> None:
        """Raise EngineUnavailable (with the reason) if `python -m <module> --help` fails."""
        if self._probed:
            if self._probe_error:
                raise EngineUnavailable(self._probe_error)
            return
        self._probed = True
        if not self.cwd.exists():
            self._probe_error = f"{self.cwd} does not exist"
            raise EngineUnavailable(self._probe_error)
        try:
            proc = subprocess.run(
                [self.python, "-m", self.module, "--help"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            self._probe_error = f"interpreter not found: {self.python}"
            raise EngineUnavailable(self._probe_error) from None
        except subprocess.TimeoutExpired:
            self._probe_error = "--help timed out"
            raise EngineUnavailable(self._probe_error) from None
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [
                "(no output)"
            ]
            self._probe_error = f"`{Path(self.python).name} -m {self.module} --help` exit {proc.returncode}: {tail[0]}"
            raise EngineUnavailable(self._probe_error)

    def run_json(self, args: list[str], timeout: int = 900) -> Any:
        proc = subprocess.run(
            [self.python, "-m", self.module, *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [
                "(no output)"
            ]
            raise RuntimeError(f"exit {proc.returncode}: {tail[0][:300]}")
        out = proc.stdout.strip()
        if not out:
            raise RuntimeError("engine printed no JSON")
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            # tolerate log lines before the JSON payload
            start = min([i for i in (out.find("{"), out.find("[")) if i >= 0] or [0])
            return json.loads(out[start:])


def default_python(engine: str) -> str:
    venv = ENGINE_CWD[engine] / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


# --------------------------------------------------------------------------- ingestion side


def _first_doc(payload: Any, file_name: str | None = None) -> dict[str, Any]:
    docs = (
        payload
        if isinstance(payload, list)
        else payload.get("documents", [payload])
        if isinstance(payload, dict)
        else []
    )
    if not docs:
        raise RuntimeError("engine returned no documents")
    if file_name:
        for d in docs:
            if (
                d.get("file_name") == file_name
                or str(d.get("file_path", "")).endswith(file_name)
                or str(d.get("source_key", "")).endswith(file_name)
            ):
                return d
        raise RuntimeError(
            f"no document for {file_name!r} in folder output ({len(docs)} docs)"
        )
    return docs[0]


class IngestionRunner:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._folder_cache: dict[Path, Any] = {}

    def extract(self, row: Row) -> dict[str, Any]:
        self.engine.probe()
        args = ["extract", "--json"]
        args += FORMAT_ARGS.get(row.format, [])
        args += row.engine_args
        if row.invoke == "folder":
            folder = row.path.parent
            if folder not in self._folder_cache:
                self._folder_cache[folder] = self.engine.run_json([*args, str(folder)])
            return _first_doc(self._folder_cache[folder], row.path.name)
        return _first_doc(self.engine.run_json([*args, str(row.path)]))


def _codes_of(doc: dict[str, Any]) -> list[str]:
    ext = doc.get("extraction") or {}
    codes = list(ext.get("warnings") or [])
    fc = ext.get("failure_code") or doc.get("failure_code")
    if fc:
        codes.append(str(fc))
    for uf in ext.get("unit_failures") or []:
        if isinstance(uf, dict) and uf.get("code"):
            codes.append(str(uf["code"]))
    return codes


def _code_matches(code: str, wanted: str) -> bool:
    return code == wanted or code.startswith(wanted + ":")


def _expand_ids(spec: Any) -> list[str] | None:
    if spec is None:
        return None
    out: list[str] = []
    for item in spec:
        if isinstance(item, dict):
            width = item.get("width", 0)
            for n in range(int(item["start"]), int(item["end"]) + 1):
                out.append(
                    f"{item['prefix']}{n:0{width}d}"
                    if width
                    else f"{item['prefix']}{n}"
                )
        else:
            out.append(str(item))
    return out


def _in_range(value: Any, spec: Any) -> bool:
    if spec is None:
        return True
    if isinstance(spec, list):
        lo, hi = spec
        return value is not None and lo <= value <= hi
    return value == spec


def check_canonical(
    row: Row, doc: dict[str, Any], others: dict[str, dict[str, Any]]
) -> list[str]:
    """Return a list of human-readable failures (empty = pass)."""
    exp = row.canonical_expected or {}
    fails: list[str] = []
    status = doc.get("extraction_status")
    want_status = exp.get("status")
    if want_status is not None:
        allowed = want_status if isinstance(want_status, list) else [want_status]
        if status not in allowed:
            fails.append(f"status {status!r} not in {allowed}")
    codes = _codes_of(doc)
    for c in exp.get("codes") or []:
        if not any(_code_matches(x, c) for x in codes):
            fails.append(f"missing code {c!r} (got {codes})")
    for c in exp.get("codes_absent") or []:
        if any(_code_matches(x, c) for x in codes):
            fails.append(f"forbidden code {c!r} present")
    any_of = exp.get("codes_any_of") or []
    if any_of and not any(_code_matches(x, c) for x in codes for c in any_of):
        fails.append(f"none of {any_of} present (got {codes})")
    structure = doc.get("structure") or {}
    for key, spec in (exp.get("structure") or {}).items():
        if spec is None:
            continue
        if not _in_range(structure.get(key), spec):
            fails.append(f"structure.{key}={structure.get(key)!r} not in {spec}")
    want_ids = _expand_ids(exp.get("requirement_ids"))
    if want_ids is not None:
        got = list(structure.get("requirement_ids") or [])
        if got != want_ids:
            fails.append(
                f"requirement_ids differ: got {len(got)} {got[:3]}…, want {len(want_ids)} {want_ids[:3]}…"
            )
    body = doc.get("body_markdown") or ""
    for kind, spec in (exp.get("markers") or {}).items():
        n = len(MARKER_RE[kind].findall(body))
        if not _in_range(n, spec):
            fails.append(f"markers.{kind}={n} not in {spec}")
    b = exp.get("body") or {}
    for s in b.get("contains") or []:
        if s not in body:
            fails.append(f"body lacks {s!r}")
    for s in b.get("not_contains") or []:
        if s in body:
            fails.append(f"body contains forbidden {s!r}")
    for s, mx in (b.get("max_occurrences") or {}).items():
        if body.count(s) > mx:
            fails.append(f"{s!r} occurs {body.count(s)}x > {mx}")
    for s, mn in (b.get("min_occurrences") or {}).items():
        if body.count(s) < mn:
            fails.append(f"{s!r} occurs {body.count(s)}x < {mn}")
    for pat, spec in (b.get("regex_counts") or {}).items():
        n = len(re.findall(pat, body, flags=re.M))
        if not _in_range(n, spec):
            fails.append(f"regex {pat!r} matched {n}x not in {spec}")
    for key, val in (exp.get("fields") or {}).items():
        if doc.get(key) != val:
            fails.append(f"{key}={doc.get(key)!r} != {val!r}")
    ref = exp.get("content_hash_equals")
    if ref:
        other = others.get(ref)
        if other is None:
            fails.append(
                f"content_hash_equals {ref}: reference row not extracted in this run"
            )
        elif other.get("content_hash") != doc.get("content_hash"):
            fails.append(f"content_hash differs from {ref}")
    return fails


# --------------------------------------------------------------------------- context side


def _chunks_of(payload: Any) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Tolerant reader for `python -m evoq_context chunk --json` output.

    The real shape (``evoq_context/cli.py::_cmd_chunk``) is one combined
    ``"chunks"`` list where each entry carries ``"level": "child"|"parent"``
    (plus ``child_count``/``parent_count`` as a cross-check) — there is no
    separate top-level ``"children"``/``"parents"`` list. Splitting on
    ``"chunks"`` unconditionally (the previous behaviour) silently treated
    every parent as an extra child and always saw zero parents — found
    running the shared corpus's parent/child + CTX-02 scenarios end to end
    during final integration; no earlier run had exercised a real (non
    chunk_only) row's parent/child golden checks at all.
    """
    if isinstance(payload, list):
        return [c for c in payload], [], {}
    if isinstance(payload.get("children"), list) or isinstance(
        payload.get("parents"), list
    ):
        return payload.get("children") or [], payload.get("parents") or [], payload
    if isinstance(payload.get("chunks"), list):
        combined = payload["chunks"]
        children = [c for c in combined if c.get("level") != "parent"]
        parents = [c for c in combined if c.get("level") == "parent"]
        return children, parents, payload
    if isinstance(payload.get("records"), list):
        children = [
            r for r in payload["records"] if _meta(r).get("chunk_level", 1) == 1
        ]
        parents = [r for r in payload["records"] if _meta(r).get("chunk_level") == 0]
        return children, parents, payload
    return [], [], payload


def _meta(chunk: dict[str, Any]) -> dict[str, Any]:
    return chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else chunk


def _text(chunk: dict[str, Any]) -> str:
    return chunk.get("text") or _meta(chunk).get("text") or ""


def _strategy(children: list[dict], payload: dict[str, Any]) -> str | None:
    dec = payload.get("decision") or {}
    if isinstance(dec, dict) and dec.get("strategy"):
        return dec["strategy"]
    if payload.get("strategy"):
        return payload["strategy"]
    if children:
        return _meta(children[0]).get("chunk_strategy")
    return None


def _ids_in(meta: dict[str, Any]) -> set[str]:
    v = meta.get("requirement_ids")
    if not v:
        return set()
    if isinstance(v, str):
        return {x.strip() for x in v.split(",") if x.strip()}
    return {str(x) for x in v}


def _tokens(chunk: dict[str, Any]) -> int | None:
    m = _meta(chunk)
    for k in ("token_estimate", "tokens", "token_count"):
        if isinstance(m.get(k), int):
            return m[k]
        if isinstance(chunk.get(k), int):
            return chunk[k]
    return None


def _pct(children: list[dict], key: str) -> float | None:
    if not children:
        return None
    n = sum(1 for c in children if _meta(c).get(key) not in (None, "", []))
    return 100.0 * n / len(children)


def check_chunking(
    row: Row,
    payload: Any,
    source_text: str,
    doc_ids: list[str] | None,
    hard_max: int,
    others: dict[str, Any],
) -> list[str]:
    exp = row.chunking_expected or {}
    fails: list[str] = []
    children, parents, top = _chunks_of(payload)
    strategy = _strategy(children, top)
    if exp.get("strategy") is not None and strategy != exp["strategy"]:
        fails.append(f"strategy {strategy!r} != {exp['strategy']!r}")
    if not _in_range(len(children), exp.get("children")):
        fails.append(f"children={len(children)} not in {exp.get('children')}")
    if not _in_range(len(parents), exp.get("parents")):
        fails.append(f"parents={len(parents)} not in {exp.get('parents')}")
    viol = [c for c in children if (_tokens(c) or 0) > hard_max]
    if (
        exp.get("hard_max_violations") is not None
        and len(viol) != exp["hard_max_violations"]
    ):
        fails.append(
            f"hard_max_violations={len(viol)} (tokens {[_tokens(c) for c in viol][:5]})"
        )
    if exp.get("req_coverage_pct") is not None:
        ids = (
            doc_ids
            if doc_ids is not None
            else _unique(re.findall(REQUIREMENT_ID_PATTERN, source_text))
        )
        covered = (
            set().union(*(_ids_in(_meta(c)) for c in children)) if children else set()
        )
        pct = (
            100.0
            if not ids
            else 100.0 * len([i for i in ids if i in covered]) / len(ids)
        )
        if pct < exp["req_coverage_pct"]:
            missing = [i for i in ids if i not in covered]
            fails.append(
                f"req_coverage {pct:.0f}% < {exp['req_coverage_pct']}% (missing {missing[:5]})"
            )
    for key, mkey in (
        ("section_path_pct", "section_path"),
        ("page_pct", "page"),
        ("sheet_pct", "sheet"),
        ("slide_pct", "slide"),
    ):
        want = exp.get(key)
        if want is None:
            continue
        got = _pct(children, mkey)
        if got is None or got < want:
            fails.append(f"{key}={got if got is None else round(got)} < {want}")
    if exp.get("topics_min") is not None:
        topics = {
            _meta(c).get("topic_id") for c in children if _meta(c).get("topic_id")
        }
        if len(topics) < exp["topics_min"]:
            fails.append(f"topics={len(topics)} < {exp['topics_min']}")
    ref = exp.get("same_texts_as")
    if ref:
        other = others.get(ref)
        if other is None:
            fails.append(f"same_texts_as {ref}: reference row not chunked in this run")
        else:
            oc, _op, _ = _chunks_of(other)
            if [_text(c) for c in oc] != [_text(c) for c in children]:
                fails.append(f"child texts differ from {ref}")
    return fails


def _unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# --------------------------------------------------------------------------- normalisation + goldens


def _normalise(obj: Any, volatile: set[str]) -> Any:
    if isinstance(obj, dict):
        return {
            k: _normalise(v, volatile)
            for k, v in sorted(obj.items())
            if k not in volatile
        }
    if isinstance(obj, list):
        return [_normalise(v, volatile) for v in obj]
    if isinstance(obj, str):
        return obj.replace(str(CORPUS_ROOT), "<corpus>").replace(
            str(REPO_ROOT), "<aeo>"
        )
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def normalise_canonical(doc: dict[str, Any]) -> dict[str, Any]:
    return _normalise(doc, VOLATILE_CANONICAL)


def normalise_chunks(payload: Any) -> Any:
    return _normalise(payload, VOLATILE_CHUNK)


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def golden_path(row_id: str, kind: str) -> Path:
    return GOLDEN_DIR / f"{row_id}.{kind}.json"


def compare_golden(
    row_id: str, kind: str, normalised: Any, *, update: bool, pin: bool
) -> str:
    p = golden_path(row_id, kind)
    text = dump(normalised)
    if not p.exists():
        if pin or update:
            GOLDEN_DIR.mkdir(exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return "pinned"
        return "missing"
    if p.read_text(encoding="utf-8") == text:
        return "match"
    if update:
        p.write_text(text, encoding="utf-8")
        return "updated"
    return "diff"


# --------------------------------------------------------------------------- orchestration


@dataclass
class Session:
    engines: list[str]
    ingestion: Engine
    context: Engine
    update_golden: bool = False
    pin: bool = False
    include_edge: bool = False
    hard_max: int = 2000
    verbose: bool = False
    canonical_out: dict[str, dict[str, Any]] = field(default_factory=dict)
    chunk_out: dict[str, Any] = field(default_factory=dict)
    outcomes: list[Outcome] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)

    def note(self, msg: str) -> None:
        if self.verbose:
            print(msg)


def _load_chunk_defaults() -> int:
    try:
        d = (
            yaml.safe_load(SCENARIOS.read_text(encoding="utf-8")).get("chunk_defaults")
            or {}
        )
        return int(d.get("hard_max", 2000))
    except Exception:  # noqa: BLE001 - defaults are a convenience
        return 2000


def run_rows(rows: list[Row], s: Session) -> None:
    WORK_DIR.mkdir(exist_ok=True)
    ing = IngestionRunner(s.ingestion)
    # pass 1: ingestion (so cross-row references like content_hash_equals resolve in pass 2)
    for row in rows:
        if row.chunk_only or "ingestion" not in s.engines:
            continue
        if row.on_demand and not row.path.exists():
            s.outcomes.append(
                Outcome(
                    row.id,
                    "ingestion",
                    "SKIP",
                    ["on-demand fixture not generated (generate_corpus.py --edge)"],
                )
            )
            continue
        if not row.path.exists() and not row.path.is_symlink():
            s.outcomes.append(
                Outcome(row.id, "ingestion", "SKIP", [f"fixture missing: {row.file}"])
            )
            continue
        try:
            doc = ing.extract(row)
        except EngineUnavailable as e:
            s.unavailable["ingestion"] = str(e)
            s.outcomes.append(
                Outcome(
                    row.id, "ingestion", "SKIP", [f"engine CLI not available yet: {e}"]
                )
            )
            continue
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            s.outcomes.append(
                Outcome(row.id, "ingestion", "FAIL", [f"engine error: {e}"])
            )
            continue
        s.canonical_out[row.id] = doc
        (WORK_DIR / f"{row.id}.canonical.json").write_text(dump(doc), encoding="utf-8")
    for row in rows:
        if (
            row.chunk_only
            or "ingestion" not in s.engines
            or row.id not in s.canonical_out
        ):
            continue
        doc = s.canonical_out[row.id]
        fails = check_canonical(row, doc, s.canonical_out)
        g = compare_golden(
            row.id,
            "canonical",
            normalise_canonical(doc),
            update=s.update_golden,
            pin=s.pin,
        )
        if g == "diff":
            fails.append(
                "golden diff (golden/%s.canonical.json) — review, then --update-golden"
                % row.id
            )
        s.outcomes.append(
            Outcome(row.id, "ingestion", "FAIL" if fails else "PASS", fails, g)
        )

    # pass 2: context
    if "context" not in s.engines:
        return
    for row in rows:
        if row.chunking_expected is None and not row.chunk_only:
            continue  # FAILED rows are never chunked
        if row.chunk_only:
            if not row.path.exists():
                s.outcomes.append(
                    Outcome(row.id, "context", "SKIP", [f"fixture missing: {row.file}"])
                )
                continue
            inp = row.path
            source_text = row.path.read_text(encoding="utf-8", errors="replace")
            doc_ids: list[str] | None = None
            source_type = ""
        else:
            doc = s.canonical_out.get(row.id)
            if doc is None:
                reason = (
                    "ingestion output unavailable"
                    if "ingestion" in s.engines
                    else "run with --engine both"
                )
                if "ingestion" in s.unavailable:
                    reason = f"ingestion engine CLI not available yet: {s.unavailable['ingestion']}"
                s.outcomes.append(Outcome(row.id, "context", "SKIP", [reason]))
                continue
            if doc.get("extraction_status") != "COMPLETE":
                s.outcomes.append(
                    Outcome(
                        row.id,
                        "context",
                        "SKIP",
                        ["document not COMPLETE — nothing to chunk"],
                    )
                )
                continue
            inp = WORK_DIR / f"{row.id}.canonical.json"
            source_text = doc.get("body_markdown") or ""
            doc_ids = (
                list((doc.get("structure") or {}).get("requirement_ids") or []) or None
            )
            # The real host (utils/context_adapter.py) derives source_type
            # from where a document actually came from (jira/confluence/
            # file), not from its file extension — selector rules like
            # "thread" (comment_thread) are gated on `source_type_in:
            # ["jira"]`. Mirror that mapping here so a Jira/Confluence row
            # driven through the standalone `chunk` CLI picks the same
            # strategy the real per-document pipeline would.
            source_type = {
                "jira_issue_json": "jira",
                "confluence_storage_xhtml": "confluence",
                "confluence_export_view_html": "confluence",
            }.get(row.format, "file")
        try:
            s.context.probe()
            # --parent-child: scenarios.yaml's chunking expectations (parents
            # ranges) assume the reconciled compose default
            # (CHUNK_PARENT_CHILD_ENABLED=true) a real build uses — the
            # standalone `chunk` CLI defaults it off for ad-hoc debugging,
            # so it must be passed explicitly here to match production.
            args = ["chunk", "--json", "--parent-child"]
            if source_type:
                args += ["--source-type", source_type]
            args.append(str(inp))
            payload = s.context.run_json(args)
        except EngineUnavailable as e:
            s.unavailable["context"] = str(e)
            s.outcomes.append(
                Outcome(
                    row.id, "context", "SKIP", [f"engine CLI not available yet: {e}"]
                )
            )
            continue
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            s.outcomes.append(
                Outcome(row.id, "context", "FAIL", [f"engine error: {e}"])
            )
            continue
        s.chunk_out[row.id] = payload
        (WORK_DIR / f"{row.id}.chunks.json").write_text(dump(payload), encoding="utf-8")
        fails = check_chunking(
            row, payload, source_text, doc_ids, s.hard_max, s.chunk_out
        )
        g = compare_golden(
            row.id,
            "chunks",
            normalise_chunks(payload),
            update=s.update_golden,
            pin=s.pin,
        )
        if g == "diff":
            fails.append(
                "golden diff (golden/%s.chunks.json) — review, then --update-golden"
                % row.id
            )
        s.outcomes.append(
            Outcome(row.id, "context", "FAIL" if fails else "PASS", fails, g)
        )


# --------------------------------------------------------------------------- reporting


def print_outcomes(s: Session) -> int:
    counts = Counter((o.engine, o.status) for o in s.outcomes)
    width = max((len(o.row_id) for o in s.outcomes), default=8)
    for o in s.outcomes:
        line = f"{o.status:4} {o.engine:9} {o.row_id:{width}} golden={o.golden}"
        if o.notes:
            line += "  | " + "; ".join(o.notes)
        print(line)
    print()
    for engine in ("ingestion", "context"):
        if engine not in s.engines:
            continue
        p, f_, k = (
            counts[(engine, "PASS")],
            counts[(engine, "FAIL")],
            counts[(engine, "SKIP")],
        )
        print(f"{engine:9}: {p} pass, {f_} fail, {k} skip")
    if s.unavailable:
        for engine, why in s.unavailable.items():
            print(
                f"\n{engine}: engine CLI not available yet — {why}\n"
                f"          (expected once lane {'L2g' if engine == 'ingestion' else 'L7'} lands "
                f"`python -m {ENGINE_MODULE[engine]}`; rows were SKIPPED, not failed)"
            )
    if any(o.status == "FAIL" for o in s.outcomes):
        return 1
    if s.unavailable:
        return 3
    return 0


def markdown_report(s: Session, rows: list[Row]) -> str:
    by = {(o.row_id, o.engine): o for o in s.outcomes}
    lines = [
        "| ID | format | canonical | chunking | golden | notes |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        a = by.get((row.id, "ingestion"))
        b = by.get((row.id, "context"))
        notes = "; ".join((a.notes if a else []) + (b.notes if b else []))
        if row.todo:
            notes = (notes + "; " if notes else "") + "todo: " + row.todo.strip()
        golden = "/".join(x.golden for x in (a, b) if x)
        lines.append(
            f"| {row.id} | {row.format} | {a.status if a else '-'} | {b.status if b else '-'} | "
            f"{golden or '-'} | {notes.replace('|', '\\|')} |"
        )
    counts = Counter((o.engine, o.status) for o in s.outcomes)
    summary = ", ".join(
        f"{e}: {counts[(e, 'PASS')]}P/{counts[(e, 'FAIL')]}F/{counts[(e, 'SKIP')]}S"
        for e in s.engines
    )
    head = f"# Corpus verification report\n\nRows: {len(rows)} · {summary}\n"
    if s.unavailable:
        head += (
            "\n"
            + "\n".join(
                f"- **{e}** engine CLI not available yet: {w}"
                for e, w in s.unavailable.items()
            )
            + "\n"
        )
    return head + "\n" + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- export-vectors


def _pseudo_embedding(text: str, dim: int = 8) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    vals = [(h[i] / 255.0) * 2 - 1 for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [round(v / norm, 6) for v in vals]


def export_vectors(s: Session, rows: list[Row], out: Path) -> int:
    """Build a `vectors_fixture.json`-shaped file (v2.1 keys, parents) from chunk outputs."""
    if not s.chunk_out:
        print(
            "export-vectors: no chunk output available"
            + (
                f" — context engine CLI not available yet: {s.unavailable['context']}"
                if "context" in s.unavailable
                else ""
            )
        )
        return 3
    vectors: list[dict[str, Any]] = []
    parents: list[dict[str, Any]] = []
    for row in rows:
        payload = s.chunk_out.get(row.id)
        if payload is None:
            continue
        children, pars, _ = _chunks_of(payload)
        for c in children:
            meta = dict(_meta(c))
            meta.setdefault("text", _text(c))
            meta.setdefault("index_contract", 2)
            meta.setdefault("chunk_level", 1)
            key = (
                meta.get("chunk_key") or c.get("key") or f"{row.id}:{len(vectors):04d}"
            )
            vectors.append(
                {
                    "key": key,
                    "vector": _pseudo_embedding(meta["text"]),
                    "metadata": _normalise(meta, VOLATILE_CHUNK),
                }
            )
        for p in pars:
            meta = dict(_meta(p))
            meta.setdefault("text", _text(p))
            meta.setdefault("chunk_level", 0)
            parents.append(
                {
                    "key": meta.get("chunk_key") or p.get("key"),
                    "metadata": _normalise(meta, VOLATILE_CHUNK),
                }
            )
    fixture = {
        "fixture_version": 1,
        "contract_revision": "2.1",
        "backend": "memory",
        "store": "memory",
        "index": "context-corpus",
        "dimension": 8,
        "distance_metric": "cosine",
        "vectors": vectors,
        "parents": parents,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(fixture), encoding="utf-8")
    print(f"export-vectors: {len(vectors)} vectors, {len(parents)} parents -> {out}")
    return 0


# --------------------------------------------------------------------------- run-e2e


def run_e2e(s: Session, rows: list[Row], report: bool) -> int:
    """corpus --check → ingestion → context → vectors fixture (→ MCP offline CONTEXT phase: L10)."""
    e2e = WORK_DIR / "e2e"
    if e2e.exists():
        shutil.rmtree(e2e)
    e2e.mkdir(parents=True)
    print("[1/5] corpus determinism check")
    proc = subprocess.run(
        [sys.executable, str(CORPUS_ROOT / "generate_corpus.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=CORPUS_ROOT,
    )
    print(
        "      "
        + (
            proc.stdout.strip().splitlines()[-1]
            if proc.stdout.strip()
            else proc.stderr.strip()[-200:]
        )
    )
    if proc.returncode != 0:
        print("      corpus drift — regenerate and review before running e2e")
        return 1
    print("[2/5] ingestion + [3/5] context over every row")
    run_rows(rows, s)
    for rid, doc in s.canonical_out.items():
        (e2e / f"{rid}.canonical.json").write_text(
            dump(normalise_canonical(doc)), encoding="utf-8"
        )
    for rid, payload in s.chunk_out.items():
        (e2e / f"{rid}.chunks.json").write_text(
            dump(normalise_chunks(payload)), encoding="utf-8"
        )
    rc = print_outcomes(s)
    print("[4/5] vectors fixture")
    rc_vec = export_vectors(s, rows, e2e / "vectors_fixture.json")
    print(
        "[5/5] MCP offline CONTEXT phase: not wired here — L10 runs "
        "`mcps/evoq_aidlc_mcp/tests/integration/test_offline_context_phase_e2e.py` against the fixture above"
    )
    if report:
        (e2e / "REPORT.md").write_text(markdown_report(s, rows), encoding="utf-8")
        print(f"report -> {e2e / 'REPORT.md'}")
    return max(rc, rc_vec if rc_vec != 0 else 0) if rc != 1 else 1


# --------------------------------------------------------------------------- CLI


def _session(args: argparse.Namespace) -> Session:
    engines = ["ingestion", "context"] if args.engine == "both" else [args.engine]
    return Session(
        engines=engines,
        ingestion=Engine(
            "ingestion", args.ingestion_python or default_python("ingestion")
        ),
        context=Engine("context", args.context_python or default_python("context")),
        update_golden=getattr(args, "update_golden", False),
        pin=(args.command == "pin"),
        include_edge=getattr(args, "edge", False),
        hard_max=_load_chunk_defaults(),
        verbose=args.verbose,
    )


def main(argv: list[str] | None = None) -> int:
    # Shared flags accepted both before AND after the subcommand (`golden.py verify --engine both`
    # from the plan's own verification section, as well as `golden.py --engine both verify`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--engine", choices=["ingestion", "context", "both"], default="both"
    )
    common.add_argument("--only", help="glob over row ids, e.g. 'PDF-*'")
    common.add_argument(
        "--ingestion-python",
        help="interpreter for `python -m evoq_ingestion` (default: aeoingestionjob/.venv)",
    )
    common.add_argument(
        "--context-python",
        help="interpreter for `python -m evoq_context` (default: aeocontextjob/.venv)",
    )
    common.add_argument("-v", "--verbose", action="store_true")

    ap = argparse.ArgumentParser(
        prog="golden.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Commands::", 1)[1] if "Commands::" in __doc__ else None,
        parents=[common],
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_pin = sub.add_parser(
        "pin",
        parents=[common],
        help="run engines and write golden/<ID>.{canonical,chunks}.json (existing pins are kept unless --update-golden)",
    )
    p_pin.add_argument("--update-golden", action="store_true")
    p_ver = sub.add_parser(
        "verify",
        parents=[common],
        help="run engines, check scenarios.yaml expectations and diff against goldens",
    )
    p_ver.add_argument(
        "--update-golden",
        action="store_true",
        help="accept differing outputs as the new goldens",
    )
    p_rep = sub.add_parser(
        "report", parents=[common], help="verify and render a Markdown results table"
    )
    p_rep.add_argument(
        "--out", type=Path, help="write the table here (default: stdout)"
    )
    p_exp = sub.add_parser(
        "export-vectors",
        parents=[common],
        help="chunk every COMPLETE/CTX row and write a vectors_fixture.json (v2.1 + parents)",
    )
    p_exp.add_argument(
        "--out", type=Path, default=CORPUS_ROOT.parent / "vectors_fixture.corpus.json"
    )
    p_e2e = sub.add_parser(
        "run-e2e",
        parents=[common],
        help="corpus → ingestion → context → vectors fixture; MCP phase is L10's",
    )
    p_e2e.add_argument("--report", action="store_true")

    args = ap.parse_args(argv)
    rows = load_rows(args.only)
    if not rows:
        print(f"no rows match {args.only!r}", file=sys.stderr)
        return 2
    s = _session(args)

    if args.command in ("pin", "verify"):
        run_rows(rows, s)
        return print_outcomes(s)
    if args.command == "report":
        run_rows(rows, s)
        rc = print_outcomes(s)
        md = markdown_report(s, rows)
        if args.out:
            args.out.write_text(md, encoding="utf-8")
            print(f"report -> {args.out}")
        else:
            print(md)
        return rc
    if args.command == "export-vectors":
        s.engines = ["ingestion", "context"]
        run_rows(rows, s)
        rc = print_outcomes(s)
        rc_vec = export_vectors(s, rows, args.out)
        return rc if rc == 1 else rc_vec or rc
    if args.command == "run-e2e":
        s.engines = ["ingestion", "context"]
        return run_e2e(s, rows, args.report)
    return 2


if __name__ == "__main__":
    sys.exit(main())
