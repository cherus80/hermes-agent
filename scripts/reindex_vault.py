#!/usr/bin/env python3
"""Reindex the configured Obsidian Vault into holographic_memory.db."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.vault_indexer import index_vault


def main() -> int:
    result = index_vault(full=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("indexed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
