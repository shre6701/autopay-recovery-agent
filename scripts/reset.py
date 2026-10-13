"""Reload the 10 fictional customers and clear all call / link / outcome history."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import Store  # noqa: E402

if __name__ == "__main__":
    Store(get_settings()).load_seed(reset=True)
    print("Reset done: 10 fictional customers loaded, history cleared.")
