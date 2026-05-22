#!/usr/bin/env python3
"""Reindex the configured Obsidian Vault into holographic_memory.db."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fcntl

from memory.vault_indexer import index_vault


LOCK_PATH = PROJECT_ROOT / ".cache" / "reindex_vault.lock"


def main() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({
                "indexed": False,
                "reason": "reindex_busy",
                "skipped": True,
                "lock_path": str(LOCK_PATH),
            }, ensure_ascii=False, indent=2))
            return 0
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        result = index_vault(full=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("indexed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
