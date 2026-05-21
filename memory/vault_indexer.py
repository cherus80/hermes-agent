"""Index an Obsidian Vault into a lightweight SQLite store."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hermes_constants import get_config_path, get_hermes_home

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
_TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_/-]+)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


@dataclass
class VaultConfig:
    enabled: bool
    path: Path
    db_path: Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_vault_config() -> VaultConfig:
    cfg_path = get_config_path()
    cfg: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.debug("vault config load failed: %s", exc)
    vault_cfg = cfg.get("vault") or {}
    hm_cfg = cfg.get("holographic_memory") or {}
    vault_path = Path(str(vault_cfg.get("path") or (get_hermes_home() / "vault"))).expanduser().resolve()
    db_path = Path(str(hm_cfg.get("db_path") or (get_hermes_home() / "holographic_memory.db"))).expanduser().resolve()
    return VaultConfig(bool(vault_cfg.get("enabled", False)), vault_path, db_path)


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).with_name("schema.sql")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    try:
        import yaml
        data = yaml.safe_load(match.group(1)) or {}
        return data if isinstance(data, dict) else {}, body
    except Exception:
        return {}, body


def _split_sections(body: str) -> list[dict[str, Any]]:
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        text = body.strip()
        return [{
            "id_suffix": "root",
            "heading": "",
            "level": 0,
            "content": text,
            "summary": _summarize_text(text),
            "position": 0,
        }] if text else []
    sections: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        heading = match.group(2).strip()
        sections.append({
            "id_suffix": f"{idx}",
            "heading": heading,
            "level": len(match.group(1)),
            "content": content,
            "summary": _summarize_text(content),
            "position": idx,
        })
    return sections


def _summarize_text(text: str, limit: int = 400) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _note_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_tags(frontmatter: dict[str, Any], text: str) -> list[str]:
    tags: list[str] = []
    raw = frontmatter.get("tags")
    if isinstance(raw, list):
        tags.extend(str(v).strip() for v in raw if str(v).strip())
    elif isinstance(raw, str) and raw.strip():
        tags.extend(v.strip() for v in raw.split(",") if v.strip())
    tags.extend(match.group(1) for match in _TAG_RE.finditer(text))
    return sorted(dict.fromkeys(tags))


def _extract_aliases(frontmatter: dict[str, Any]) -> list[str]:
    raw = frontmatter.get("aliases") or []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    return []


def _title_from_path(frontmatter: dict[str, Any], note_path: Path) -> str:
    title = str(frontmatter.get("title") or "").strip()
    return title or note_path.stem


def index_note(note_path: Path, cfg: VaultConfig | None = None) -> dict[str, Any]:
    cfg = cfg or load_vault_config()
    ensure_schema(cfg.db_path)
    if not note_path.exists():
        return {"indexed": False, "reason": "missing"}
    note_path = note_path.resolve()
    try:
        note_path.relative_to(cfg.path.resolve())
    except ValueError as exc:
        raise ValueError(f"note path escapes vault root: {note_path}") from exc
    text = note_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _parse_frontmatter(text)
    rel_path = str(note_path.relative_to(cfg.path))
    title = _title_from_path(frontmatter, note_path)
    tags = _extract_tags(frontmatter, text)
    aliases = _extract_aliases(frontmatter)
    sections = _split_sections(body)
    headings = [s["heading"] for s in sections if s["heading"]]
    summary = _summarize_text(body)
    updated_at = _utc_now()
    digest = _note_hash(text)
    links = sorted(dict.fromkeys(match.group(1).strip() for match in _WIKILINK_RE.finditer(text)))

    conn = sqlite3.connect(cfg.db_path)
    try:
        conn.execute("DELETE FROM vault_notes_fts WHERE path = ?", (rel_path,))
        conn.execute("DELETE FROM vault_sections WHERE note_path = ?", (rel_path,))
        conn.execute("DELETE FROM vault_sections_fts WHERE note_path = ?", (rel_path,))
        conn.execute(
            """
            INSERT INTO vault_notes(path, title, aliases_json, tags_json, headings_json, summary, hash, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              title=excluded.title,
              aliases_json=excluded.aliases_json,
              tags_json=excluded.tags_json,
              headings_json=excluded.headings_json,
              summary=excluded.summary,
              hash=excluded.hash,
              updated_at=excluded.updated_at
            """,
            (
                rel_path,
                title,
                json.dumps(aliases, ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False),
                json.dumps(headings, ensure_ascii=False),
                summary,
                digest,
                updated_at,
            ),
        )
        conn.execute(
            "INSERT INTO vault_notes_fts(path, title, summary, tags, aliases) VALUES(?, ?, ?, ?, ?)",
            (rel_path, title, summary, " ".join(tags), " ".join(aliases)),
        )
        for section in sections:
            section_id = f"{rel_path}#{section['id_suffix']}"
            conn.execute(
                """
                INSERT INTO vault_sections(id, note_path, heading, level, content, summary, position, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id,
                    rel_path,
                    section["heading"],
                    section["level"],
                    section["content"],
                    section["summary"],
                    section["position"],
                    updated_at,
                ),
            )
            conn.execute(
                "INSERT INTO vault_sections_fts(section_id, note_path, heading, summary, content) VALUES(?, ?, ?, ?, ?)",
                (
                    section_id,
                    rel_path,
                    section["heading"],
                    section["summary"],
                    section["content"],
                ),
            )
        for target in links:
            link_id = f"{rel_path}->{target}"
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_links(id, from_id, to_id, link_type, weight, created_at)
                VALUES(?, ?, ?, 'wikilink', 0.8, ?)
                """,
                (link_id, rel_path, target, updated_at),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "indexed": True,
        "path": rel_path,
        "title": title,
        "tags": tags,
        "aliases": aliases,
        "sections": len(sections),
    }


def index_vault(full: bool = False, cfg: VaultConfig | None = None) -> dict[str, Any]:
    cfg = cfg or load_vault_config()
    if not cfg.enabled:
        return {"indexed": False, "reason": "vault_disabled"}
    if not cfg.path.exists():
        return {"indexed": False, "reason": "vault_missing", "path": str(cfg.path)}
    ensure_schema(cfg.db_path)
    count = 0
    changed = 0
    conn = sqlite3.connect(cfg.db_path)
    try:
        existing = {
            row[0]: row[1]
            for row in conn.execute("SELECT path, hash FROM vault_notes")
        }
    finally:
        conn.close()
    for note_path in sorted(cfg.path.rglob("*.md")):
        if ".obsidian" in note_path.parts:
            continue
        count += 1
        rel = str(note_path.relative_to(cfg.path))
        digest = _note_hash(note_path.read_text(encoding="utf-8", errors="replace"))
        if full or existing.get(rel) != digest:
            index_note(note_path, cfg)
            changed += 1
    return {"indexed": True, "notes_scanned": count, "notes_updated": changed, "db_path": str(cfg.db_path)}


def search_notes(query: str, *, limit: int = 10, cfg: VaultConfig | None = None) -> list[dict[str, Any]]:
    cfg = cfg or load_vault_config()
    ensure_schema(cfg.db_path)
    query = (query or "").strip()
    if not query:
        return []
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT n.path, n.title, n.summary, n.tags_json, n.aliases_json
            FROM vault_notes_fts f
            JOIN vault_notes n ON n.path = f.path
            WHERE vault_notes_fts MATCH ?
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        if not rows:
            like_query = f"%{query.lower()}%"
            rows = conn.execute(
                """
                SELECT path, title, summary, tags_json, aliases_json
                FROM vault_notes
                WHERE lower(path) LIKE ?
                   OR lower(title) LIKE ?
                   OR lower(summary) LIKE ?
                   OR lower(tags_json) LIKE ?
                   OR lower(aliases_json) LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (like_query, like_query, like_query, like_query, like_query, limit),
            ).fetchall()
    finally:
        conn.close()
    results = []
    for row in rows:
        results.append({
            "path": row["path"],
            "title": row["title"],
            "summary": row["summary"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "aliases": json.loads(row["aliases_json"] or "[]"),
        })
    return results
