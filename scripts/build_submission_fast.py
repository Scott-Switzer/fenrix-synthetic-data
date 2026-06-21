#!/usr/bin/env python3
# ruff: noqa: E402, F403, I001
"""Thin CLI wrapper for the sanitized public submission builder."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fenrix_synthetic.submission_fast import *  # noqa: F403
from fenrix_synthetic.submission_package import *  # noqa: F403
from fenrix_synthetic.submission_package import main
from fenrix_synthetic.submission_sources import *  # noqa: F403


if __name__ == "__main__":
    raise SystemExit(main())
