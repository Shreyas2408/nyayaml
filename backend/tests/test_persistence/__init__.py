"""Mocked persistence tests, runnable by module from the repository root."""

import sys
from pathlib import Path

# The committed infrastructure uses the canonical `app` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
