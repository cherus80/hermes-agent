from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from memory.vault_indexer import VaultConfig, ensure_schema, index_note


def test_index_note_retries_through_short_db_lock(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note_path = vault_path / "note.md"
    note_path.write_text("# Test\n\nhello", encoding="utf-8")
    db_path = tmp_path / "holographic_memory.db"
    cfg = VaultConfig(enabled=True, path=vault_path, db_path=db_path)
    ensure_schema(db_path)

    blocker = sqlite3.connect(
        str(db_path),
        timeout=1.0,
        isolation_level=None,
        check_same_thread=False,
    )
    blocker.execute("PRAGMA journal_mode=WAL")
    blocker.execute("BEGIN IMMEDIATE")

    def release_lock() -> None:
        time.sleep(0.2)
        blocker.commit()
        blocker.close()

    releaser = threading.Thread(target=release_lock)
    releaser.start()
    try:
        result = index_note(note_path, cfg)
    finally:
        releaser.join()

    assert result["indexed"] is True
    check_conn = sqlite3.connect(str(db_path))
    try:
        row = check_conn.execute(
            "SELECT title, summary FROM vault_notes WHERE path = ?",
            ("note.md",),
        ).fetchone()
    finally:
        check_conn.close()
    assert row is not None
    assert row[0] == "note"
    assert "hello" in row[1].lower()
