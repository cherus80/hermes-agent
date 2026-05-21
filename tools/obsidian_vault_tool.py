#!/usr/bin/env python3
"""Tools for working with an Obsidian Vault mirrored onto the VPS."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_config_path
from memory.vault_indexer import index_note, index_vault, load_vault_config, search_notes
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


def _load_yaml_config() -> dict[str, Any]:
    cfg_path = get_config_path()
    if not cfg_path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("vault tool config load failed: %s", exc)
        return {}


def _vault_enabled() -> bool:
    return load_vault_config().enabled


def _vault_root() -> Path:
    cfg = load_vault_config()
    if not cfg.enabled:
        raise ValueError("Vault integration is disabled in config.")
    return cfg.path.resolve()


def _safe_note_path(note_path: str) -> Path:
    root = _vault_root()
    raw = (note_path or "").strip()
    if not raw:
        raise ValueError("note path is required")
    if raw.startswith("/"):
        candidate = Path(raw).expanduser().resolve()
    else:
        candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("note path escapes vault root") from exc
    return candidate


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _daily_note_path(date_str: Optional[str] = None) -> Path:
    cfg = _load_yaml_config().get("vault") or {}
    daily_dir = str(cfg.get("daily_notes_dir") or "Daily")
    current = date_str or datetime.now().strftime("%Y-%m-%d")
    return _safe_note_path(f"{daily_dir}/{current}.md")


def _project_note_path(project_key: str) -> Path:
    cfg = _load_yaml_config().get("vault") or {}
    projects_dir = str(cfg.get("projects_dir") or "Projects")
    safe_key = re.sub(r"[^A-Za-z0-9._ -]+", "-", project_key.strip()).strip() or "Project"
    return _safe_note_path(f"{projects_dir}/{safe_key}.md")


def _artifacts_dir() -> Path:
    cfg = _load_yaml_config().get("vault") or {}
    artifacts_dir = str(cfg.get("artifacts_dir") or "Artifacts")
    return _safe_note_path(artifacts_dir)


def _relative_note_path(path: Path) -> str:
    return str(path.resolve().relative_to(_vault_root()))


def _safe_artifact_name(name: str, fallback: str = "artifact") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", (name or "").strip()).strip(" .")
    return cleaned or fallback


def vault_search(query: str, limit: int = 10) -> str:
    if not query.strip():
        return tool_error("query is required")
    try:
        results = search_notes(query.strip(), limit=max(1, min(limit, 25)))
        return _render_json({"query": query, "results": results, "count": len(results)})
    except Exception as exc:
        logger.warning("vault_search failed: %s", exc)
        return tool_error(str(exc))


def vault_read_note(path: str) -> str:
    try:
        note_path = _safe_note_path(path)
        if not note_path.exists():
            return tool_error(f"note not found: {path}")
        content = note_path.read_text(encoding="utf-8", errors="replace")
        return _render_json({
            "path": _relative_note_path(note_path),
            "content": content,
        })
    except Exception as exc:
        logger.warning("vault_read_note failed: %s", exc)
        return tool_error(str(exc))


def vault_create_note(path: str, title: str = "", content: str = "") -> str:
    try:
        note_path = _safe_note_path(path)
        if note_path.exists():
            return tool_error(f"note already exists: {path}")
        _ensure_parent(note_path)
        body = content
        if title.strip() and not body.strip():
            body = f"# {title.strip()}\n"
        note_path.write_text(body, encoding="utf-8")
        index_note(note_path)
        return _render_json({"created": True, "path": _relative_note_path(note_path)})
    except Exception as exc:
        logger.warning("vault_create_note failed: %s", exc)
        return tool_error(str(exc))


def vault_append_note(path: str, content: str) -> str:
    if not content.strip():
        return tool_error("content is required")
    try:
        note_path = _safe_note_path(path)
        _ensure_parent(note_path)
        existing = note_path.read_text(encoding="utf-8", errors="replace") if note_path.exists() else ""
        separator = "\n" if existing and not existing.endswith("\n") else ""
        note_path.write_text(existing + separator + content, encoding="utf-8")
        index_note(note_path)
        return _render_json({"updated": True, "path": _relative_note_path(note_path)})
    except Exception as exc:
        logger.warning("vault_append_note failed: %s", exc)
        return tool_error(str(exc))


def vault_update_section(path: str, heading: str, content: str, mode: str = "replace") -> str:
    if not heading.strip():
        return tool_error("heading is required")
    try:
        note_path = _safe_note_path(path)
        _ensure_parent(note_path)
        existing = note_path.read_text(encoding="utf-8", errors="replace") if note_path.exists() else ""
        heading_line = f"## {heading.strip()}"
        pattern = re.compile(
            rf"(?ms)^##\s+{re.escape(heading.strip())}\s*\n.*?(?=^##\s+|\Z)"
        )
        replacement = f"{heading_line}\n{content.rstrip()}\n"
        if pattern.search(existing):
            if mode == "append":
                current = pattern.search(existing).group(0).rstrip() + "\n" + content.rstrip() + "\n"
                updated = pattern.sub(current, existing, count=1)
            else:
                updated = pattern.sub(replacement, existing, count=1)
        else:
            prefix = "" if not existing.strip() else ("\n" if existing.endswith("\n") else "\n\n")
            updated = existing + prefix + replacement
        note_path.write_text(updated, encoding="utf-8")
        index_note(note_path)
        return _render_json({"updated": True, "path": _relative_note_path(note_path), "heading": heading.strip()})
    except Exception as exc:
        logger.warning("vault_update_section failed: %s", exc)
        return tool_error(str(exc))


def vault_daily_note(date: str = "", append_content: str = "") -> str:
    try:
        note_path = _daily_note_path(date.strip() or None)
        _ensure_parent(note_path)
        if not note_path.exists():
            note_path.write_text(f"# {note_path.stem}\n", encoding="utf-8")
        if append_content.strip():
            existing = note_path.read_text(encoding="utf-8", errors="replace")
            separator = "\n" if existing and not existing.endswith("\n") else ""
            note_path.write_text(existing + separator + append_content.rstrip() + "\n", encoding="utf-8")
        index_note(note_path)
        return _render_json({"path": _relative_note_path(note_path), "created_or_updated": True})
    except Exception as exc:
        logger.warning("vault_daily_note failed: %s", exc)
        return tool_error(str(exc))


def vault_project_note(project_key: str, append_content: str = "") -> str:
    if not project_key.strip():
        return tool_error("project_key is required")
    try:
        note_path = _project_note_path(project_key)
        _ensure_parent(note_path)
        if not note_path.exists():
            note_path.write_text(
                f"---\ntype: project\nproject_key: {project_key.strip()}\nstatus: active\n---\n\n# {project_key.strip()}\n",
                encoding="utf-8",
            )
        if append_content.strip():
            existing = note_path.read_text(encoding="utf-8", errors="replace")
            separator = "\n" if existing and not existing.endswith("\n") else ""
            note_path.write_text(existing + separator + append_content.rstrip() + "\n", encoding="utf-8")
        index_note(note_path)
        return _render_json({"path": _relative_note_path(note_path), "created_or_updated": True})
    except Exception as exc:
        logger.warning("vault_project_note failed: %s", exc)
        return tool_error(str(exc))


def vault_reindex(full: bool = False) -> str:
    try:
        result = index_vault(full=full)
        return _render_json(result)
    except Exception as exc:
        logger.warning("vault_reindex failed: %s", exc)
        return tool_error(str(exc))


def vault_save_artifact(
    filename: str,
    source_path: str = "",
    content: str = "",
    content_base64: str = "",
    folder: str = "",
    note_path: str = "",
    note_heading: str = "Artifacts",
) -> str:
    if not filename.strip():
        return tool_error("filename is required")
    if sum(bool(v.strip()) for v in [source_path, content, content_base64]) != 1:
        return tool_error("provide exactly one of source_path, content, or content_base64")

    try:
        artifact_root = _artifacts_dir()
        artifact_root.mkdir(parents=True, exist_ok=True)
        target_dir = artifact_root
        if folder.strip():
            safe_folder = re.sub(r"[^A-Za-z0-9/_ .-]+", "-", folder.strip()).strip("/")
            target_dir = _safe_note_path(f"{_relative_note_path(artifact_root)}/{safe_folder}")
            target_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _safe_artifact_name(filename, fallback="artifact")
        target_path = (target_dir / safe_name).resolve()
        try:
            target_path.relative_to(_vault_root())
        except ValueError as exc:
            raise ValueError("artifact path escapes vault root") from exc

        if source_path.strip():
            src = Path(source_path).expanduser().resolve()
            if not src.exists() or not src.is_file():
                return tool_error(f"source file not found: {source_path}")
            shutil.copy2(src, target_path)
        elif content_base64.strip():
            payload = base64.b64decode(content_base64.encode("ascii"))
            target_path.write_bytes(payload)
        else:
            target_path.write_text(content, encoding="utf-8")

        mime_type, _ = mimetypes.guess_type(target_path.name)
        relative_artifact = _relative_note_path(target_path)
        result: dict[str, Any] = {
            "saved": True,
            "path": relative_artifact,
            "bytes": target_path.stat().st_size,
            "mime_type": mime_type or "application/octet-stream",
            "wikilink": f"![[{relative_artifact}]]",
            "markdown_link": f"[{target_path.name}]({relative_artifact})",
        }

        if note_path.strip():
            link_line = f"- {result['markdown_link']}"
            if mime_type and mime_type.startswith("image/"):
                link_line = f"- {result['wikilink']}"
            note_update = vault_update_section(note_path, note_heading, link_line, mode="append")
            if note_update.startswith("Error:"):
                return note_update
            result["note_updated"] = True
            result["note_path"] = note_path
            result["note_heading"] = note_heading

        return _render_json(result)
    except Exception as exc:
        logger.warning("vault_save_artifact failed: %s", exc)
        return tool_error(str(exc))


VAULT_SEARCH_SCHEMA = {
    "name": "vault_search",
    "description": "Search the Obsidian Vault mirrored on disk. Returns matching notes with path, summary, tags, and aliases.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for note title, summary, tags, or aliases."},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    },
}

VAULT_READ_SCHEMA = {
    "name": "vault_read_note",
    "description": "Read a note from the Obsidian Vault by relative path.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}

VAULT_CREATE_SCHEMA = {
    "name": "vault_create_note",
    "description": "Create a new note in the Obsidian Vault.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path"],
    },
}

VAULT_APPEND_SCHEMA = {
    "name": "vault_append_note",
    "description": "Append content to an existing Vault note, creating it if needed.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}

VAULT_UPDATE_SECTION_SCHEMA = {
    "name": "vault_update_section",
    "description": "Replace or append a level-2 section in a Vault note.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "heading": {"type": "string"},
            "content": {"type": "string"},
            "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
        },
        "required": ["path", "heading", "content"],
    },
}

VAULT_DAILY_SCHEMA = {
    "name": "vault_daily_note",
    "description": "Create or append to a daily note in the Vault. Date format: YYYY-MM-DD. Omit date for today.",
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string"},
            "append_content": {"type": "string"},
        },
        "required": [],
    },
}

VAULT_PROJECT_SCHEMA = {
    "name": "vault_project_note",
    "description": "Create or append to a project note in the Vault using the configured Projects directory.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_key": {"type": "string"},
            "append_content": {"type": "string"},
        },
        "required": ["project_key"],
    },
}

VAULT_REINDEX_SCHEMA = {
    "name": "vault_reindex",
    "description": "Reindex the mirrored Obsidian Vault into holographic_memory.db. Use full=true for a complete rebuild.",
    "parameters": {
        "type": "object",
        "properties": {
            "full": {"type": "boolean", "default": False},
        },
        "required": [],
    },
}

VAULT_SAVE_ARTIFACT_SCHEMA = {
    "name": "vault_save_artifact",
    "description": "Save a file artifact into the Obsidian Vault Artifacts directory. Can copy an existing local file, save plain text, or decode base64 content. Optionally appends a link into a note section.",
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {"type": "string"},
            "source_path": {"type": "string", "description": "Absolute or local path of an existing file to copy into the vault."},
            "content": {"type": "string", "description": "Plain text content to save as a new file."},
            "content_base64": {"type": "string", "description": "Base64-encoded file bytes to save."},
            "folder": {"type": "string", "description": "Optional subfolder inside Artifacts, for example project-name or images."},
            "note_path": {"type": "string", "description": "Optional vault note to update with a link to the saved artifact."},
            "note_heading": {"type": "string", "default": "Artifacts"},
        },
        "required": ["filename"],
    },
}


registry.register(name="vault_search", toolset="vault", schema=VAULT_SEARCH_SCHEMA, handler=lambda args, **kw: vault_search(args.get("query", ""), args.get("limit", 10)), check_fn=_vault_enabled, emoji="🧭")
registry.register(name="vault_read_note", toolset="vault", schema=VAULT_READ_SCHEMA, handler=lambda args, **kw: vault_read_note(args.get("path", "")), check_fn=_vault_enabled, emoji="📓", max_result_size_chars=100_000)
registry.register(name="vault_create_note", toolset="vault", schema=VAULT_CREATE_SCHEMA, handler=lambda args, **kw: vault_create_note(args.get("path", ""), args.get("title", ""), args.get("content", "")), check_fn=_vault_enabled, emoji="📝")
registry.register(name="vault_append_note", toolset="vault", schema=VAULT_APPEND_SCHEMA, handler=lambda args, **kw: vault_append_note(args.get("path", ""), args.get("content", "")), check_fn=_vault_enabled, emoji="➕")
registry.register(name="vault_update_section", toolset="vault", schema=VAULT_UPDATE_SECTION_SCHEMA, handler=lambda args, **kw: vault_update_section(args.get("path", ""), args.get("heading", ""), args.get("content", ""), args.get("mode", "replace")), check_fn=_vault_enabled, emoji="🧩")
registry.register(name="vault_daily_note", toolset="vault", schema=VAULT_DAILY_SCHEMA, handler=lambda args, **kw: vault_daily_note(args.get("date", ""), args.get("append_content", "")), check_fn=_vault_enabled, emoji="📅")
registry.register(name="vault_project_note", toolset="vault", schema=VAULT_PROJECT_SCHEMA, handler=lambda args, **kw: vault_project_note(args.get("project_key", ""), args.get("append_content", "")), check_fn=_vault_enabled, emoji="📁")
registry.register(name="vault_reindex", toolset="vault", schema=VAULT_REINDEX_SCHEMA, handler=lambda args, **kw: vault_reindex(bool(args.get("full", False))), check_fn=_vault_enabled, emoji="🗂️")
registry.register(name="vault_save_artifact", toolset="vault", schema=VAULT_SAVE_ARTIFACT_SCHEMA, handler=lambda args, **kw: vault_save_artifact(args.get("filename", ""), args.get("source_path", ""), args.get("content", ""), args.get("content_base64", ""), args.get("folder", ""), args.get("note_path", ""), args.get("note_heading", "Artifacts")), check_fn=_vault_enabled, emoji="📎")
