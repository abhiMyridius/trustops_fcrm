#!/usr/bin/env python3
"""Scan this project with Opengrep (free SAST engine) and write a JSON report.

The counterpart of sonarqubescript.py: scan only, never edits code. The command line,
severity mapping and issue format follow evoqcodescanservice (scanners/opengrep.py), so a
report here reads the same as one from the service.

  python3 opengrepscript.py                       # scan this folder, write ops/reports/opengrep/<name>-<date>.json
  python3 opengrepscript.py --previous <old.json> # also mark each issue new / still_open and count the fixed ones
  python3 opengrepscript.py --fail-on critical    # exit 1 when a finding of that severity or worse exists

First run: it downloads the Opengrep binary (pinned version, sha256 checked) and builds the
rule pack from GitLab's sast-rules (pinned commit, with the service's scripts/build_rule_pack.py)
into ~/.cache/opengrep. Neither step sends any project data anywhere. Later runs are offline.

Settings (environment): OPENGREP_BIN, OPENGREP_RULES_DIR, OPENGREP_RULE_GROUPS,
OPENGREP_SERVICE_DIR (where build_rule_pack.py lives), OPENGREP_EXCLUDE, OPENGREP_MIN_SEVERITY,
OPENGREP_TIMEOUT_SECONDS, OPENGREP_JOBS, PROJECT_DIR, REPORT_DIR, PROJECT_NAME, OPENGREP_KEEP_RAW=1.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
CACHE = Path(os.getenv("OPENGREP_CACHE", "~/.cache/opengrep")).expanduser()

# Pinned like the service's Dockerfile: change both together, after checking the release signature.
OPENGREP_VERSION = "v2.0.0"
OPENGREP_SHA256 = "37e93f4da5fd04a18c5c1febbdd12f7e98dfccd4d0c9cf2593bcb25eeb462da0"
OPENGREP_URL = f"https://github.com/opengrep/opengrep/releases/download/{OPENGREP_VERSION}/opengrep_linux_x86"
GITLAB_RULES_COMMIT = "83144bd2ec7d3200cda96347fa923f81c8ad8413"

SERVICE_DIR = Path(
    os.getenv("OPENGREP_SERVICE_DIR", "/root/repos/services/evoqcodescanservice")
)
RULE_GROUPS = os.getenv("OPENGREP_RULE_GROUPS", "mit,apache,lgpl,lgpl-cc,gpl,gitlab-ee")
# What is not scanned: dependencies, build output, generated and minified files, tests (as the service
# does), plus this repo's mocks, e2e specs, documents and earlier scan reports.
EXCLUDE = os.getenv(
    "OPENGREP_EXCLUDE",
    "node_modules,.venv,venv,env,__pycache__,dist,build,out,target,.git,.next,.nuxt,coverage,htmlcov,"
    ".pytest_cache,.mypy_cache,.tox,vendor,bower_components,*.min.js,*.min.css,*.map,"
    "tests,test,__tests__,*.test.*,*.spec.*,test_*.py,*_test.py,*_test.go,conftest.py,"
    "e2e,mocks,docs,reports,.cache,mockServiceWorker.js",
)
MIN_SEVERITY = os.getenv("OPENGREP_MIN_SEVERITY", "info")
TIMEOUT_S = int(os.getenv("OPENGREP_TIMEOUT_SECONDS", "600"))
RULE_TIMEOUT_S = int(os.getenv("OPENGREP_RULE_TIMEOUT_SECONDS", "10"))
JOBS = int(os.getenv("OPENGREP_JOBS", "2"))
MAX_MEMORY_MB = int(os.getenv("OPENGREP_MAX_MEMORY_MB", "2048"))
MAX_TARGET_BYTES = int(os.getenv("OPENGREP_MAX_TARGET_BYTES", "1000000"))

SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "critical": 3, "blocker": 4}
SECURITY_SEVERITY = {
    "critical": "blocker",
    "high": "critical",
    "medium": "major",
    "low": "minor",
    "info": "info",
}
LEVEL_SEVERITY = {"ERROR": "critical", "WARNING": "major", "INFO": "info"}
OK_EXIT_CODES = (
    0,
    1,
    2,
)  # 0 clean, 1 findings, 2 some rule or file problem (JSON is still printed)


class ScanError(Exception):
    """The scan could not run or its result cannot be trusted (never reported as '0 issues')."""


# ------------------------------------------------------------------ setup
def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:  # noqa: S310 - fixed https url
        shutil.copyfileobj(resp, out)
    tmp.replace(target)


def ensure_binary() -> Path:
    """OPENGREP_BIN, else `opengrep` on PATH, else the pinned release in the cache (downloaded once)."""
    wanted = os.getenv("OPENGREP_BIN")
    if wanted:
        found = shutil.which(wanted) or (wanted if Path(wanted).is_file() else None)
        if not found:
            raise ScanError(f"OPENGREP_BIN={wanted} is not an executable file")
        return Path(found)
    on_path = shutil.which("opengrep")
    if on_path:
        return Path(on_path)
    binary = CACHE / OPENGREP_VERSION / "opengrep"
    if not binary.is_file():
        print(f"Downloading Opengrep {OPENGREP_VERSION} (about 150 MB, once) ...")
        _download(OPENGREP_URL, binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != OPENGREP_SHA256:
        binary.unlink(missing_ok=True)
        raise ScanError(
            f"the downloaded Opengrep does not match the pinned sha256 ({digest[:12]} ...); removed it"
        )
    binary.chmod(0o755)
    return binary


def clean_env(work_dir: str) -> dict[str, str]:
    """What the program may see: no tokens, no project settings."""
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": work_dir,
        "TMPDIR": work_dir,
        "LANG": "C.UTF-8",
    }


def version_of(binary: Path) -> str:
    try:
        out = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            env=clean_env(tempfile.gettempdir()),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(
            f"the opengrep program does not run ({type(exc).__name__})"
        ) from exc
    lines = out.stdout.strip().splitlines()
    if out.returncode != 0 or not lines:
        raise ScanError("the opengrep program does not answer --version")
    return lines[-1][:40]


def load_manifest(rules_dir: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(
            (rules_dir / "RULES_MANIFEST.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return (
        data
        if isinstance(data, dict) and isinstance(data.get("groups"), dict)
        else None
    )


def ensure_rules(binary: Path) -> tuple[Path, dict[str, Any], list[str]]:
    """(rules folder, manifest, groups to scan with). Builds the pack once with the service's script."""
    rules_dir = Path(
        os.getenv("OPENGREP_RULES_DIR")
        or CACHE / "rules" / GITLAB_RULES_COMMIT[:12] / "gitlab"
    )
    manifest = load_manifest(rules_dir)
    if manifest is None:
        builder = SERVICE_DIR / "scripts" / "build_rule_pack.py"
        if not builder.is_file():
            raise ScanError(
                f"no rule pack in {rules_dir} and {builder} is not there to build one "
                "(set OPENGREP_RULES_DIR or OPENGREP_SERVICE_DIR)"
            )
        print(
            f"Building the rule pack from GitLab sast-rules {GITLAB_RULES_COMMIT[:12]} (once) ..."
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(builder),
                "--commit",
                GITLAB_RULES_COMMIT,
                "--groups",
                RULE_GROUPS,
                "--out",
                str(rules_dir),
                "--opengrep",
                str(binary),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        if proc.returncode != 0:
            raise ScanError(
                f"building the rule pack failed: {' '.join(proc.stderr.split())[-300:]}"
            )
        manifest = load_manifest(rules_dir)
    if manifest is None:
        raise ScanError(f"the rule pack in {rules_dir} has no RULES_MANIFEST.json")
    have = manifest["groups"]
    wanted = [g.strip() for g in RULE_GROUPS.split(",") if g.strip()]
    used = [
        g
        for g in wanted
        if int((have.get(g) or {}).get("rules", 0)) > 0 and (rules_dir / g).is_dir()
    ]
    if not used:
        raise ScanError(f"none of the rule groups {wanted} is in {rules_dir}")
    return rules_dir, manifest, used


# ------------------------------------------------------------------ run
def build_command(binary: Path, group_dirs: list[Path], code_dir: Path) -> list[str]:
    """The switches that stop the scanned code from hiding its own findings; do not drop them."""
    cmd = [str(binary), "scan"]
    for d in group_dirs:
        cmd += ["--config", str(d)]
    cmd += [
        "--json",
        "--quiet",
        "--no-rewrite-rule-ids",  # the rule's own id, stable when the rule pack moves
        "--no-git-ignore",  # the same result inside or outside a git repo
        "--disable-nosem",  # a `# nosemgrep` comment cannot hide a finding
        "--x-ignore-semgrepignore-files",  # nor can an ignore file in the code
        "--exclude-minified-files",
        f"--max-target-bytes={MAX_TARGET_BYTES}",
        f"--timeout={RULE_TIMEOUT_S}",
        f"--jobs={JOBS}",
        f"--max-memory={MAX_MEMORY_MB}",
    ]
    cmd += [f"--exclude={p.strip()}" for p in EXCLUDE.split(",") if p.strip()]
    cmd.append(str(code_dir))
    return cmd


def execute(cmd: list[str], work_dir: Path) -> tuple[int, dict[str, Any]]:
    """Run in its own process group (a timeout kills everything it started); return (exit code, parsed JSON)."""
    out_path, err_path = work_dir / "out.json", work_dir / "err.txt"
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=work_dir,
            env=clean_env(str(work_dir)),
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise ScanError(f"opengrep timed out after {TIMEOUT_S} s") from exc
        finally:
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    try:
        raw = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        tail = " ".join(
            err_path.read_text(encoding="utf-8", errors="replace")[-1500:].split()
        )
        raise ScanError(
            f"opengrep gave no readable result (exit {proc.returncode}): {tail[-300:]}"
        )
    if proc.returncode not in OK_EXIT_CODES:
        raise ScanError(f"opengrep failed (exit {proc.returncode})")
    return proc.returncode, raw


def _kind(error: dict[str, Any]) -> str:
    kind = error.get("type")
    if isinstance(kind, list) and kind:
        kind = kind[0]
    return str(kind or "").strip()


def check_errors(raw: dict[str, Any]) -> dict[str, Any]:
    """Tolerate (and count) rule-parse errors, timeouts and warnings; anything else fails the scan,
    so a broken setup can never look like '0 issues'."""
    rule_errors, warnings, timeouts = 0, 0, []
    for err in raw.get("errors") or []:
        if not isinstance(err, dict):
            continue
        kind, level = _kind(err).lower(), str(err.get("level", "")).lower()
        if kind == "rule parse error":
            rule_errors += 1
        elif kind == "timeout":
            timeouts.append(str(err.get("path") or err.get("rule_id") or "")[:200])
        elif level in ("warn", "warning", "info"):
            warnings += 1
        else:
            first = " ".join(str(err.get("message", "")).split())[:300]
            raise ScanError(
                f"opengrep reported an error ({_kind(err) or 'unknown'}): {first}"
            )
    return {
        "rule_errors": rule_errors,
        "timeouts": timeouts[:10],
        "timeout_count": len(timeouts),
        "warnings": warnings,
    }


# ------------------------------------------------------------------ convert
def _short(value: Any, limit: int = 80, cap: int = 5) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(v)[:limit] for v in values if v not in (None, "")][:cap]


def _severity(extra: dict[str, Any]) -> str:
    sec = (
        str((extra.get("metadata") or {}).get("security-severity") or "")
        .strip()
        .lower()
    )
    return SECURITY_SEVERITY.get(sec) or LEVEL_SEVERITY.get(
        str(extra.get("severity", "")).upper(), "info"
    )


def _issue_type(meta: dict[str, Any]) -> str:
    category = str(meta.get("category") or "security").lower()
    if category in ("security", "vulnerability"):
        return "vulnerability"
    return "bug" if category in ("correctness", "bug") else "smell"


def _relative(path: str, code_dir: Path) -> Optional[str]:
    root = os.path.normpath(code_dir)
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(root, path))
    if full == root or not full.startswith(root + os.sep):
        return None
    return os.path.relpath(full, root).replace(os.sep, "/")


def fingerprint(rule: str, file: str, anchor: str) -> str:
    """Same problem -> same id even when its line moves (identical to the service's)."""
    raw = "\x1f".join(["opengrep", rule, file, anchor or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def convert(
    raw: dict[str, Any], code_dir: Path, min_severity: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Opengrep results -> the common issue format. Only a hash of the matched code line is kept."""
    floor = SEVERITY_RANK.get(min_severity.lower(), 0)
    issues: list[dict[str, Any]] = []
    outside = filtered = 0
    for res in raw.get("results") or []:
        extra = res.get("extra") or {}
        meta = extra.get("metadata") or {}
        file = _relative(str(res.get("path", "")), code_dir)
        if file is None:
            outside += 1
            continue
        severity = _severity(extra)
        if SEVERITY_RANK[severity] < floor:
            filtered += 1
            continue
        rule = str(res.get("check_id", ""))
        start = res.get("start") or {}
        matched = " ".join(str(extra.get("lines") or "").split())
        message = " ".join(str(extra.get("message", "")).split())
        short = " ".join(str(meta.get("shortDescription") or "").split())
        text = f"{short}: {message}" if short and short not in message else message
        code_hash = hashlib.sha1((matched or message).encode("utf-8")).hexdigest()[:16]
        refs = {
            k: v
            for k, v in (
                ("cwe", _short(meta.get("cwe"))),
                ("owasp", _short(meta.get("owasp"))),
            )
            if v
        }
        issues.append(
            {
                "tool": "opengrep",
                "type": _issue_type(meta),
                "severity": severity,
                "file": file,
                "line": start.get("line"),
                "rule": rule,
                "message": text[:500],
                "key": f"{rule}:{start.get('line')}:{start.get('col')}",
                "code_hash": code_hash,
                "refs": refs,
                "fingerprint": fingerprint(rule, file, code_hash),
            }
        )
    issues.sort(
        key=lambda i: (-SEVERITY_RANK[i["severity"]], i["file"], i["line"] or 0)
    )
    return issues, {"outside_code_folder": outside, "below_min_severity": filtered}


def compare(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> int:
    """Mark each issue new / still_open (matches are counted: two identical lines are two issues);
    return how many of the previous issues are gone."""
    left = Counter(p.get("fingerprint") for p in previous)
    for issue in current:
        if left[issue["fingerprint"]] > 0:
            left[issue["fingerprint"]] -= 1
            issue["change"] = "still_open"
        else:
            issue["change"] = "new"
    return sum(left.values())


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--code",
        type=Path,
        default=Path(os.getenv("PROJECT_DIR", HERE)),
        help="folder to scan",
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="report file (default: <REPORT_DIR>/<name>-<date>.json)",
    )
    ap.add_argument(
        "--previous",
        type=Path,
        help="an earlier report: mark issues new / still_open, count fixed",
    )
    ap.add_argument(
        "--fail-on",
        choices=[s for s in SEVERITY_RANK if s != "info"],
        help="exit 1 when an issue of this severity or worse exists",
    )
    args = ap.parse_args()

    code = args.code.resolve()
    name = os.getenv("PROJECT_NAME", "TrustOpsAI-FCRM")
    report_dir = Path(os.getenv("REPORT_DIR", code / "ops" / "reports" / "opengrep"))
    out = args.out or report_dir / f"{name}-{datetime.now().strftime('%Y-%m-%d')}.json"
    if not code.is_dir():
        print(f"Not a folder: {code}")
        return 2
    try:
        binary = ensure_binary()
        version = version_of(binary)
        rules_dir, manifest, groups = ensure_rules(binary)
        print(
            f"Opengrep {version}; rules {manifest.get('commit', '?')[:12]}, groups {', '.join(groups)}"
        )
        with tempfile.TemporaryDirectory(
            prefix="opengrep-"
        ) as tmp:  # never inside the scanned code
            cmd = build_command(binary, [rules_dir / g for g in groups], code)
            print(f"Scanning {code} ...")
            started = time.monotonic()
            _, raw = execute(cmd, Path(tmp))
            seconds = round(time.monotonic() - started, 1)
            counts = check_errors(raw)
            if (
                os.getenv("OPENGREP_KEEP_RAW") == "1"
            ):  # holds matched code lines: keep it out of git
                raw_out = out.with_name(out.stem + "-raw.json")
                raw_out.parent.mkdir(parents=True, exist_ok=True)
                raw_out.write_text(json.dumps(raw, indent=2), encoding="utf-8")
                print(f"Raw output (matched code lines, do not commit): {raw_out}")
    except (ScanError, OSError, subprocess.SubprocessError) as exc:
        print(f"Scan failed: {exc}")
        return 2

    issues, left_out = convert(raw, code, MIN_SEVERITY)
    fixed = None
    if args.previous:
        try:
            previous = json.loads(args.previous.read_text(encoding="utf-8")).get(
                "issues", []
            )
        except (OSError, ValueError):
            print(f"Cannot read --previous {args.previous}")
            return 2
        fixed = compare(issues, previous)
    scanned = len((raw.get("paths") or {}).get("scanned") or [])
    by_sev = Counter(i["severity"] for i in issues)
    report = {
        "project": name,
        "tool": "opengrep",
        "version": version,
        "rule_pack_commit": manifest.get("commit"),
        "groups": groups,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "exclude": [p for p in EXCLUDE.split(",") if p],
        "files_scanned": scanned,
        "seconds": seconds,
        "summary": {
            "issue_count": len(issues),
            "by_severity": dict(by_sev),
            "by_type": dict(Counter(i["type"] for i in issues)),
            **({"fixed": fixed} if fixed is not None else {}),
        },
        "scan_info": {**counts, **left_out},
        "issues": issues,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"\nFiles scanned: {scanned}   Time: {seconds} s   Rule errors tolerated: {counts['rule_errors']}"
    )
    print(f"Issues: {len(issues)}  by severity: {dict(by_sev)}")
    if fixed is not None:
        print(
            f"Against {args.previous.name}: new {sum(i['change'] == 'new' for i in issues)}, "
            f"still open {sum(i['change'] == 'still_open' for i in issues)}, fixed {fixed}"
        )
    for issue in issues[:25]:
        print(
            f"- [{issue['severity']}] {issue['file']}:{issue['line']}  {issue['rule'].split('.')[-1]}\n"
            f"    {issue['message'][:150]}"
        )
    if len(issues) > 25:
        print(f"... and {len(issues) - 25} more in the report")
    print(f"\nReport: {out}")
    if args.fail_on and any(
        SEVERITY_RANK[i["severity"]] >= SEVERITY_RANK[args.fail_on] for i in issues
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
