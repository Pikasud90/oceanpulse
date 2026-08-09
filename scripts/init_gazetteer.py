#!/usr/bin/env python3
"""Standalone gazetteer builder.

Equivalent to `run.py gazetteer`, kept as a separate script so the launchers
and service installers can build the port database without starting the web
interface.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from run import main  # noqa: E402


if __name__ == "__main__":
    argv = ["gazetteer"] + sys.argv[1:]
    raise SystemExit(main(argv))
