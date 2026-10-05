"""``python -m evoq_context ...`` entry point."""

from __future__ import annotations

import sys

from evoq_context.cli import main

if __name__ == "__main__":
    sys.exit(main())
