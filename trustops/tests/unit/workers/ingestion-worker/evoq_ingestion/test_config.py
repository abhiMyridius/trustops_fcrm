"""Tests for core/config.py — config dataclasses.

Ported (selectively) from AEO/ingestion/tests/unit/logics/ingestion/test_config.py.
The credential / Jira / Confluence / key-validation cases were pruned with
those dataclasses on the TrustOps clone (UPSTREAM.md).
"""

from __future__ import annotations

from evoq_ingestion.core.config import UploadsIngestionConfig


class TestUploadsIngestionConfig:
    def test_defaults(self) -> None:
        cfg = UploadsIngestionConfig()
        assert cfg.enabled is True
        assert cfg.folder_path is None
        assert cfg.files is None
        assert ".pdf" in cfg.extraction.allowed_extensions
