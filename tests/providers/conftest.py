from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
for package in (
    "provider-faster-whisper",
    "provider-funasr",
    "provider-ultralytics",
):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
