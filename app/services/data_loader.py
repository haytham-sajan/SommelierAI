from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def load_wines(dataset_path: Path) -> List[Dict[str, Any]]:
    raw = dataset_path.read_text(encoding="utf-8")
    wines = json.loads(raw)
    if not isinstance(wines, list):
        raise ValueError("Dataset JSON must be a list of wine objects.")
    return wines

