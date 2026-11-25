#!/usr/bin/env python3
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import requests

# Configuration
SONAR_HOST_URL = os.getenv(
    "SONAR_HOST_URL", "https://sonarqube.myridiusevoq.com"
).rstrip("/")
# The token is never kept in this file: set SONAR_TOKEN, or put it alone in
# ~/.config/sonar/token (chmod 600).
_TOKEN_FILE = os.path.expanduser(os.getenv("SONAR_TOKEN_FILE", "~/.config/sonar/token"))
SONAR_TOKEN = os.getenv("SONAR_TOKEN") or (
    Path(_TOKEN_FILE).read_text(encoding="utf-8").strip()
    if os.path.exists(_TOKEN_FILE)
    else ""
)
PROJECT_BASE_DIR = os.getenv("PROJECT_BASE_DIR", "/root/pyworkspace/codecommit")
# Project key (no spaces) and display name on the SonarQube dashboard.
# Unset, the key is evoqapp-<repo folder> and the name equals the key, as before.
SONAR_PROJECT_KEY = os.getenv("SONAR_PROJECT_KEY")
SONAR_PROJECT_NAME = os.getenv("SONAR_PROJECT_NAME")
# REPORT_ONLY=1 scans and lists issues but never runs Claude to edit the code.
REPORT_ONLY = os.getenv("REPORT_ONLY", "0") == "1"
# Overrides the language-based exclusions below when set.
SONAR_EXCLUSIONS = os.getenv("SONAR_EXCLUSIONS")
# Maps detected dominant language -> file extensions counted for detection
LANGUAGE_EXTENSIONS = {
    "javascript": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
    "java": {".java"},
    "python": {".py"},
}
# Maps detected dominant language -> sonar.exclusions pattern
LANGUAGE_EXCLUSIONS = {
    "javascript": "**/node_modules/**,**/dist/**,**/build/**",
    "java": "**/target/**,**/build/**",
    "python": "**/venv/**,**/.venv/**,**/__pycache__/**",
}
DEFAULT_EXCLUSIONS = "**/node_modules/**,**/target/**,**/build/**"


def detect_language(repo_path):
    """Walks repo_path and returns the dominant language based on file extension counts."""
    counts = dict.fromkeys(LANGUAGE_EXTENSIONS, 0)
    ext_to_lang = {
        ext: lang for lang, exts in LANGUAGE_EXTENSIONS.items() for ext in exts
    }
    for _root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d
            for d in dirs
            if d
            not in {
                ".git",
                "node_modules",
                "venv",
                ".venv",
                "target",
                "build",
                "dist",
                "__pycache__",
            }
        ]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            lang = ext_to_lang.get(ext)
            if lang:
                counts[lang] += 1
    dominant = max(counts, key=counts.get)
    if counts[dominant] == 0:
        return None
    return dominant


def get_exclusions_for_repo(repo_path):
    """Returns the sonar.exclusions pattern matching the repo's dominant language."""
    if SONAR_EXCLUSIONS:
        print(f"Using exclusions from SONAR_EXCLUSIONS: {SONAR_EXCLUSIONS}")
        return SONAR_EXCLUSIONS
    language = detect_language(repo_path)
    exclusions = LANGUAGE_EXCLUSIONS.get(language, DEFAULT_EXCLUSIONS)
    print(
        f"Detected dominant language: {language or 'unknown'}. Using exclusions: {exclusions}"
    )
    return exclusions


def run_command(cmd, cwd=None):
    """Executes a shell command and yields output/errors."""
    if isinstance(cmd, list):
        cmd = shlex.join(cmd)  # quotes arguments with spaces, such as a project name
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    stdout, stderr = process.communicate()
    return process.returncode, stdout, stderr


def run_command_streaming(cmd, cwd=None):
    """Executes a command (list of args, run without a shell), printing each stdout line live as it arrives."""
    process = subprocess.Popen(
        cmd,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        bufsize=1,
    )
    lines = []
    for line in process.stdout:
        print(line, end="")
        lines.append(line)
    process.wait()
    return process.returncode, "".join(lines)


def ensure_project_registered(project_key, project_name=None):
    """Registers the project on SonarQube if it doesn't exist yet; no-op if it's already there."""
    show_url = f"{SONAR_HOST_URL}/api/components/show"
    res = requests.get(
        show_url, params={"component": project_key}, auth=(SONAR_TOKEN, ""), timeout=10
    )
    if res.status_code == 200:
        print(f"Project '{project_key}' already registered. Will update via scan.")
        return True
    print(f"Project '{project_key}' not found. Registering it...")
    create_url = f"{SONAR_HOST_URL}/api/projects/create"
    res = requests.post(
        create_url,
        data={"project": project_key, "name": project_name or project_key},
        auth=(SONAR_TOKEN, ""),
        timeout=10,
    )
    if res.status_code == 200:
        print(f"Project '{project_key}' registered successfully.")
        return True
    print(f"Failed to register project '{project_key}': {res.status_code} - {res.text}")
    return False


def run_sonar_scanner(repo_path, project_key, project_name=None):
    """Triggers sonar-scanner to send analysis to the remote SonarQube server."""
    print(
        f"\n[1/4] Running SonarScanner for project: {project_key} for path {repo_path}..."
    )
    exclusions = get_exclusions_for_repo(repo_path)
    # Base command targeting remote SonarQube instance
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        # The token goes in through the environment (`-e NAME` copies it from this process),
        # never on the command line, where `ps` and the process list would show it.
        "-e",
        "SONAR_TOKEN",
        "-v",
        f"{repo_path}:/usr/src",
        "sonarsource/sonar-scanner-cli",
        f"-Dsonar.host.url={SONAR_HOST_URL}",
        f"-Dsonar.projectKey={project_key}",
    ]
    if not os.path.exists(os.path.join(repo_path, "sonar-project.properties")):
        # No properties file: fall back to the language-based defaults. With one, a
        # command-line flag would override what the file says.
        cmd += ["-Dsonar.sources=.", f"-Dsonar.exclusions={exclusions}"]
    if project_name:
        cmd.append(f"-Dsonar.projectName={project_name}")
    os.environ["SONAR_TOKEN"] = (
        SONAR_TOKEN  # read by the docker client for `-e SONAR_TOKEN`
    )
    code, stdout, stderr = run_command(cmd, cwd=repo_path)
    if code != 0:
        print(f"Error running SonarScanner:\n{stderr}")
        return None
    print("Scan completed successfully and submitted to remote server.")
    match = re.search(r"api/ce/task\?id=([\w-]+)", stdout)
    if not match:
        print("Warning: could not find CE task id in scanner output.")
        return ""
    return match.group(1)


def wait_for_report_processing(task_id, timeout_sec=600):
    """Polls SonarQube API until the specific Compute Engine task finishes processing."""
    print(
        "[2/4] Waiting for remote SonarQube server to finish background processing..."
    )
    if not task_id:
        print("No CE task id to track; falling back to a fixed wait.")
        time.sleep(10)
        return False
    url = f"{SONAR_HOST_URL}/api/ce/task"
    params = {"id": task_id}
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        try:
            res = requests.get(url, params=params, auth=(SONAR_TOKEN, ""), timeout=10)
            print(f"Polling SonarQube CE task status: {res.status_code}")
            if res.status_code == 200:
                status = res.json().get("task", {}).get("status")
                print(f"Task '{task_id}' status: {status}")
                if status in ("SUCCESS", "FAILED", "CANCELED"):
                    print("Remote processing complete.")
                    return status == "SUCCESS"
        except (requests.RequestException, ValueError) as e:
            print(f"Polling error: {e}")
        time.sleep(3)
    print("Timed out waiting for remote SonarQube processing.")
    return False


def fetch_open_issues(
    project_key,
    # ,severities="BLOCKER,CRITICAL,MAJOR"
    severities="BLOCKER,HIGH,MEDIUM",
):
    """Fetches list of unfixed code quality issues from SonarQube Web API."""
    print(f"[3/4] Querying unfixed issues ({severities})...")
    url = f"{SONAR_HOST_URL}/api/issues/search"
    params = {
        "componentKeys": project_key,
        # "severities": severities,
        "impactSeverities": severities,
        "statuses": "OPEN,REOPENED",
        "ps": 100,  # Page size up to 100 issues
    }
    res = requests.get(url, params=params, auth=(SONAR_TOKEN, ""), timeout=15)
    if res.status_code != 200:
        print(f"Failed to fetch issues: {res.status_code} - {res.text}")
        return []
    issues = res.json().get("issues", [])

    formatted_issues = []
    for issue in issues:
        component = issue.get("component", "")
        # Strip project key prefix to get relative file path
        file_path = component.split(":", 1)[-1] if ":" in component else component

        formatted_issues.append(
            {
                "key": issue.get("key"),
                "severity": issue.get("severity"),
                "file": file_path,
                "line": issue.get("line"),
                "message": issue.get("message"),
                "type": issue.get("type"),
            }
        )
    return formatted_issues


def fix_issues_with_claude(repo_path, issues):
    """Feeds SonarQube issue list to Claude Code CLI to execute fixes."""
    print(f"[4/4] Prompting Claude Code CLI to fix {len(issues)} issue(s)...")
    prompt = (
        "You are tasked with fixing SonarQube code quality and security issues in this repository.\n"
        "Here is the list of issues reported by SonarQube:\n\n"
        f"{json.dumps(issues, indent=2)}\n\n"
        "Please modify the relevant files to fix ALL listed issues. "
        "Do not introduce breaking changes or introduce new bugs.\n\n"
        "Before fixing each issue, print a line in the form "
        "'FIXING [<severity>] <file>:<line> - <message>' so progress can be tracked live. "
        "After fixing it, print 'FIXED <key>'."
    )
    # Invoke Claude Code non-interactively using `-p`, streaming output live
    cmd = [
        "claude",
        "-p",
        prompt,
        "--allowedTools",
        "Edit,Read,Bash,Write",
    ]  # Grants Claude permissions to read/edit files non-interactively
    code, _ = run_command_streaming(cmd, cwd=repo_path)
    if code != 0:
        print(f"\nClaude Code exited with non-zero status: {code}")


def sonarqube_auto_fix_loop(repo_name, max_iterations=3):
    """Main execution loop."""
    repo_path = os.path.join(PROJECT_BASE_DIR, repo_name)
    project_key = SONAR_PROJECT_KEY or f"evoqapp-{repo_name}"
    project_name = SONAR_PROJECT_NAME
    if not SONAR_TOKEN:
        print(
            "No SonarQube token: set SONAR_TOKEN or write it to ~/.config/sonar/token."
        )
        sys.exit(1)
    if not os.path.exists(repo_path):
        print(f"Repository path does not exist: {repo_path}")
        sys.exit(1)
    print(f"Starting SonarQube Auto-Fix Loop for target: {repo_path}")
    # Step 0: Register the project if it doesn't exist yet, otherwise it'll be updated by the scan.
    if not ensure_project_registered(project_key, project_name):
        print("Aborting due to project registration failure.")
        sys.exit(1)
    for iteration in range(1, max_iterations + 1):
        print(
            f"\n=================== ITERATION {iteration}/{max_iterations} ==================="
        )
        # Step 1: Run local scanner to send report to remote SonarQube
        task_id = run_sonar_scanner(repo_path, project_key, project_name)
        if task_id is None:
            print("Aborting due to SonarScanner failure.")
            break
        # Step 2: Wait for remote server to compute metrics
        if not wait_for_report_processing(task_id):
            print("Proceeding anyway, but issue list might be stale...")
        # Step 3: Fetch active issues
        issues = fetch_open_issues(project_key)
        print(f"Found {len(issues)} unresolved issue(s) after iteration {iteration}.")
        print(json.dumps(issues, indent=2))

        if not issues:
            print(
                f"🎉 Success! No BLOCKER, CRITICAL, or MAJOR issues remaining after {iteration} iteration(s)."
            )
            return
        print(f"Found {len(issues)} unresolved issue(s).")
        if REPORT_ONLY:
            print(
                f"\nREPORT_ONLY=1: no fixes applied. Dashboard: {SONAR_HOST_URL}/dashboard?id={project_key}"
            )
            return
        # Step 4: Fix issues with Claude Code CLI
        fix_issues_with_claude(repo_path, issues)
    print("\nReached max iterations. Some issues may require manual intervention.")


if __name__ == "__main__":
    # Example usage: Replace 'my-target-repo' with directory name inside /mnt/mynewapp/
    TARGET_REPO = os.getenv("TARGET_REPO_NAME", "evoqportalservice_v2")
    sonarqube_auto_fix_loop(repo_name=TARGET_REPO, max_iterations=2)
