"""Tests for cli.py — ``python -m evoq_ingestion extract ...``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evoq_ingestion.cli import main


class TestExtractCommand:
    def test_single_file_markdown_summary(self, tmp_path: Path, capsys):
        f = tmp_path / "a.txt"
        f.write_text("Hello REQ-001 world.")
        rc = main(["extract", str(f), "--engine", "inprocess"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "a.txt" in out
        assert "requirement_ids: 1" in out

    def test_folder_json_output(self, tmp_path: Path, capsys):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.md").write_text("# Title")
        rc = main(["extract", str(tmp_path), "--engine", "inprocess", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        docs = json.loads(out)
        assert len(docs) == 2
        names = {d["file_name"] for d in docs}
        assert names == {"a.txt", "b.md"}

    def test_nonexistent_path_errors(self, tmp_path: Path, capsys):
        rc = main(["extract", str(tmp_path / "nope"), "--engine", "inprocess"])
        assert rc == 2

    def test_unknown_command_requires_subcommand(self):
        with pytest.raises(SystemExit):
            main([])

    def test_single_file_failure_is_a_document_not_a_raise(
        self, tmp_path: Path, capsys
    ):
        """Unlike ``run.extract_file``, the CLI never raises for a single-file
        failure — an unsupported extension comes back as a printed
        UNSUPPORTED document (exit 0), matching ``extract_folder``'s
        never-silently-vanish contract."""
        f = tmp_path / "a.xyz"
        f.write_text("hello")
        rc = main(["extract", str(f), "--engine", "inprocess", "--json"])
        assert rc == 0
        docs = json.loads(capsys.readouterr().out)
        assert docs[0]["extraction_status"] == "UNSUPPORTED"
        assert "unsupported_extension" in docs[0]["extraction"]["warnings"][0]

    def test_per_file_timeout_seconds_surfaces_extraction_timeout(
        self, tmp_path: Path, capsys
    ):
        f = tmp_path / "a.txt"
        f.write_text("x" * 10_000)
        rc = main(
            [
                "extract",
                str(f),
                "--engine",
                "inprocess",
                "--per-file-timeout-seconds",
                "0.0000001",
                "--json",
            ]
        )
        assert rc == 0
        docs = json.loads(capsys.readouterr().out)
        assert docs[0]["extraction_status"] == "FAILED"
        assert docs[0]["extraction"]["failure_code"] == "extraction_timeout"

    def test_output_writes_full_json_to_file(self, tmp_path: Path, capsys):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        out_path = tmp_path / "result.json"
        rc = main(
            ["extract", str(f), "--engine", "inprocess", "--output", str(out_path)]
        )
        assert rc == 0
        assert out_path.exists()
        payload = json.loads(out_path.read_text())
        assert payload["documents"][0]["file_name"] == "a.txt"
        assert "Full JSON written to" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "argv",
        [
            ["--raw-format", "adf_json"],
            ["--ai-summary"],
            ["--engine", "mcp"],
        ],
    )
    def test_pruned_flags_are_rejected(self, tmp_path: Path, argv: list[str]):
        """TrustOps prune: the Atlassian raw-format path, the LiteLLM summary
        pass and the MCP engine are gone, so argparse rejects their flags."""
        f = tmp_path / "a.txt"
        f.write_text("hello")
        with pytest.raises(SystemExit) as exc:
            main(["extract", str(f), *argv])
        assert exc.value.code == 2
