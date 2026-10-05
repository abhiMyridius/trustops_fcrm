"""``python -m evoq_ingestion ...`` entry point."""

from __future__ import annotations

import sys

from evoq_ingestion.cli import main

if __name__ == "__main__":
    sys.exit(main())
