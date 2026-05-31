"""Persistent state for the browser_operator plugin.

The browser extension talks to the HTTP server, while Hermes tools read and
write the same state file. Keeping the protocol file-backed makes the first
MVP resilient to process restarts and avoids optional runtime dependencies.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover
    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


MAX_TEXT_CHARS = 20000
MAX_ELEMENTS = 500
MAX_REGIONS = 120
MAX_ACTIONS = 200
MAX_RESULTS = 200

_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir() -> Path:
    return Path(get_hermes_home()) / "browser-operator"


def extension_dir() -> Path:
    return Path(__file__).resolve().parent / "extension"


def state_path() -> Path:
    return state_dir() / "state.json"


def audit_path() -> Path:
    return state_dir() / "audit.jsonl"


def _default_state() -> Dict[str, Any]:
    return {
        "version": 2,
        "snapshots": {},
        "tabs": {},
        "actions": {},
        "results": {},
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def _read_state_unlocked() -> Dict[str, Any]:
    path = state_path()
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(".json.bad")
        try:
            path.replace(backup)
        except OSError:
            pass
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    base = _default_state()
    base.update(data)
    for key in ("snapshots", "tabs", "actions", "results"):
        if not isinstance(base.get(key), dict):
            base[key] = {}
    return base


def read_state() -> Dict[str, Any]:
    with _LOCK:
        return _read_state_unlocked()


def _write_state_unlocked(data: Dict[str, Any]) -> None:
    data["updated_at"] = utc_now()
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_audit_unlocked(event: str, payload: Dict[str, Any]) -> None:
    entry = {
        "ts": utc_now(),
        "event": event,
        "payload": payload,
    }
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _trim_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > limit:
        return text[:limit] + "\n[truncated]"
    return text


def _safe_element(raw: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "ref",
        "tag",
        "role",
        "type",
        "text",
        "ariaLabel",
        "placeholder",
        "label",
        "name",
        "id",
        "selector",
        "href",
        "visible",
        "enabled",
        "rect",
        "nearText",
        "hasValue",
    }
    out = {key: raw.get(key) for key in allowed if key in raw}
    for key in ("text", "ariaLabel", "placeholder", "label", "name", "id", "nearText"):
        if key in out:
            out[key] = _trim_text(out.get(key), 300)
    if "href" in out:
        out["href"] = _trim_text(out.get("href"), 500)
    return out


def _safe_region(raw: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "ref",
        "tag",
        "role",
        "selector",
        "text",
        "ariaLabel",
        "label",
        "rect",
        "links",
        "numbers",
    }
    out = {key: raw.get(key) for key in allowed if key in raw}
    for key in ("text", "ariaLabel", "label"):
        if key in out:
            out[key] = _trim_text(out.get(key), 2000)
    if isinstance(out.get("links"), list):
        safe_links = []
        for link in out["links"][:20]:
            if not isinstance(link, dict):
                continue
            safe_links.append(
                {
                    "text": _trim_text(link.get("text"), 300),
                    "href": _trim_text(link.get("href"), 1000),
                }
            )
        out["links"] = safe_links
    else:
        out["links"] = []
    if isinstance(out.get("numbers"), list):
        out["numbers"] = [_trim_text(item, 100) for item in out["numbers"][:40]]
    else:
        out["numbers"] = []
    return out


def normalize_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(raw.get("sessionId") or raw.get("session_id") or "default")
    tab_id = str(raw.get("tabId") or raw.get("tab_id") or "active")
    elements = raw.get("elements") if isinstance(raw.get("elements"), list) else []
    safe_elements = [
        _safe_element(item) for item in elements[:MAX_ELEMENTS] if isinstance(item, dict)
    ]
    regions = raw.get("regions") if isinstance(raw.get("regions"), list) else []
    safe_regions = [
        _safe_region(item) for item in regions[:MAX_REGIONS] if isinstance(item, dict)
    ]
    browser_tab = raw.get("browserTab") if isinstance(raw.get("browserTab"), dict) else {}
    snapshot = {
        "sessionId": session_id,
        "tabId": tab_id,
        "browserTab": browser_tab,
        "extensionVersion": _trim_text(raw.get("extensionVersion"), 100),
        "url": _trim_text(raw.get("url"), 2000),
        "title": _trim_text(raw.get("title"), 500),
        "text": _trim_text(raw.get("text"), MAX_TEXT_CHARS),
        "elements": safe_elements,
        "regions": safe_regions,
        "authSignals": raw.get("authSignals") if isinstance(raw.get("authSignals"), dict) else {},
        "viewport": raw.get("viewport") if isinstance(raw.get("viewport"), dict) else {},
        "createdAt": raw.get("createdAt") or utc_now(),
        "receivedAt": utc_now(),
    }
    return snapshot


def upsert_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_snapshot(raw)
    key = f"{snapshot['sessionId']}:{snapshot['tabId']}"
    with _LOCK:
        data = _read_state_unlocked()
        data["snapshots"][snapshot["sessionId"]] = snapshot
        data["tabs"][key] = snapshot
        _write_state_unlocked(data)
        _append_audit_unlocked(
            "snapshot",
            {
                "sessionId": snapshot["sessionId"],
                "tabId": snapshot["tabId"],
                "url": snapshot.get("url", ""),
                "elements": len(snapshot.get("elements", [])),
            },
        )
    return snapshot


def latest_snapshot(session_id: str = "default") -> Optional[Dict[str, Any]]:
    data = read_state()
    snap = data.get("snapshots", {}).get(session_id)
    return snap if isinstance(snap, dict) else None


def get_tab_snapshot(session_id: str = "default", tab_id: str = "") -> Optional[Dict[str, Any]]:
    if not tab_id:
        return latest_snapshot(session_id)
    data = read_state()
    snap = data.get("tabs", {}).get(f"{session_id}:{tab_id}")
    return snap if isinstance(snap, dict) else None


def _tab_summary(snap: Dict[str, Any]) -> Dict[str, Any]:
    browser_tab = snap.get("browserTab") if isinstance(snap.get("browserTab"), dict) else {}
    return {
        "sessionId": snap.get("sessionId"),
        "tabId": snap.get("tabId"),
        "chromeTabId": browser_tab.get("chromeTabId"),
        "windowId": browser_tab.get("windowId"),
        "active": bool(browser_tab.get("active")),
        "pinned": bool(browser_tab.get("pinned")),
        "url": snap.get("url"),
        "title": snap.get("title"),
        "receivedAt": snap.get("receivedAt"),
        "extensionVersion": snap.get("extensionVersion"),
        "elements": len(snap.get("elements") or []),
        "regions": len(snap.get("regions") or []),
        "textPreview": (snap.get("text") or "")[:500],
    }


def list_tabs(session_id: str = "default", query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    data = read_state()
    query_l = (query or "").lower().strip()
    rows: List[Dict[str, Any]] = []
    for snap in data.get("tabs", {}).values():
        if not isinstance(snap, dict):
            continue
        if str(snap.get("sessionId") or "default") != session_id:
            continue
        haystack = "\n".join(
            str(part or "")
            for part in (
                snap.get("tabId"),
                snap.get("title"),
                snap.get("url"),
                snap.get("text"),
            )
        ).lower()
        if query_l and query_l not in haystack:
            continue
        rows.append(_tab_summary(snap))
    rows.sort(key=lambda item: str(item.get("receivedAt") or ""), reverse=True)
    return rows[: max(1, min(int(limit or 20), 100))]


def find_tabs(session_id: str = "default", query: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    words = [part for part in (query or "").lower().split() if part]
    rows: List[Dict[str, Any]] = []
    data = read_state()
    for snap in data.get("tabs", {}).values():
        if not isinstance(snap, dict):
            continue
        if str(snap.get("sessionId") or "default") != session_id:
            continue
        haystack = "\n".join(
            str(part or "")
            for part in (
                snap.get("tabId"),
                snap.get("title"),
                snap.get("url"),
                snap.get("text"),
            )
        ).lower()
        score = 0.0
        if query and query.lower() in haystack:
            score += 5.0
        for word in words:
            if word in haystack:
                score += 1.0
        if not words:
            score = 1.0
        if score <= 0:
            continue
        item = _tab_summary(snap)
        item["score"] = round(score, 3)
        rows.append(item)
    rows.sort(key=lambda item: (float(item.get("score") or 0), str(item.get("receivedAt") or "")), reverse=True)
    return rows[: max(1, min(int(limit or 8), 25))]


def list_sessions() -> List[Dict[str, Any]]:
    data = read_state()
    sessions: List[Dict[str, Any]] = []
    for session_id, snap in sorted(data.get("snapshots", {}).items()):
        if not isinstance(snap, dict):
            continue
        sessions.append(
            {
                "sessionId": session_id,
                "tabId": snap.get("tabId"),
                "url": snap.get("url"),
                "title": snap.get("title"),
                "receivedAt": snap.get("receivedAt"),
                "elements": len(snap.get("elements") or []),
            }
        )
    return sessions


def queue_action(action: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(action.get("sessionId") or action.get("session_id") or "default")
    action_id = str(action.get("id") or uuid.uuid4())
    normalized = {
        "id": action_id,
        "sessionId": session_id,
        "tabId": str(action.get("tabId") or action.get("tab_id") or "active"),
        "type": str(action.get("type") or action.get("action") or "highlight"),
        "target": str(action.get("target") or ""),
        "value": action.get("value"),
        "reason": str(action.get("reason") or ""),
        "requiresUserConfirm": bool(action.get("requiresUserConfirm", True)),
        "status": "queued",
        "createdAt": utc_now(),
    }
    with _LOCK:
        data = _read_state_unlocked()
        data["actions"][action_id] = normalized
        _prune_unlocked(data)
        _write_state_unlocked(data)
        _append_audit_unlocked(
            "action_queued",
            {
                "id": action_id,
                "sessionId": session_id,
                "tabId": normalized["tabId"],
                "type": normalized["type"],
                "target": normalized["target"],
                "requiresUserConfirm": normalized["requiresUserConfirm"],
            },
        )
    return normalized


def next_action(session_id: str = "default", tab_id: str = "active") -> Optional[Dict[str, Any]]:
    with _LOCK:
        data = _read_state_unlocked()
        candidates = sorted(
            (
                item
                for item in data.get("actions", {}).values()
                if isinstance(item, dict)
                and item.get("sessionId") == session_id
                and item.get("status") == "queued"
                and item.get("tabId", "active") in ("active", "*", tab_id)
            ),
            key=lambda item: item.get("createdAt", ""),
        )
        if not candidates:
            return None
        action = candidates[0]
        action["status"] = "dispatched"
        action["dispatchedAt"] = utc_now()
        data["actions"][action["id"]] = action
        _write_state_unlocked(data)
        return action


def record_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    action_id = str(raw.get("actionId") or raw.get("action_id") or raw.get("id") or "")
    if not action_id:
        action_id = str(uuid.uuid4())
    result = {
        "actionId": action_id,
        "sessionId": str(raw.get("sessionId") or raw.get("session_id") or "default"),
        "tabId": str(raw.get("tabId") or raw.get("tab_id") or ""),
        "ok": bool(raw.get("ok")),
        "status": str(raw.get("status") or ("done" if raw.get("ok") else "failed")),
        "error": _trim_text(raw.get("error"), 1000),
        "message": _trim_text(raw.get("message"), 1000),
        "element": raw.get("element") if isinstance(raw.get("element"), dict) else None,
        "createdAt": utc_now(),
    }
    with _LOCK:
        data = _read_state_unlocked()
        data["results"][action_id] = result
        action = data.get("actions", {}).get(action_id)
        if isinstance(action, dict):
            if not result["tabId"]:
                result["tabId"] = str(action.get("tabId") or "")
            action["status"] = result["status"]
            action["completedAt"] = result["createdAt"]
            action["ok"] = result["ok"]
            data["actions"][action_id] = action
        _prune_unlocked(data)
        _write_state_unlocked(data)
        _append_audit_unlocked(
            "action_result",
            {
                "id": action_id,
                "sessionId": result["sessionId"],
                "tabId": result["tabId"],
                "ok": result["ok"],
                "status": result["status"],
                "error": result["error"],
            },
        )
    return result


def get_result(action_id: str) -> Optional[Dict[str, Any]]:
    data = read_state()
    result = data.get("results", {}).get(action_id)
    return result if isinstance(result, dict) else None


def _prune_unlocked(data: Dict[str, Any]) -> None:
    for key, limit in (("actions", MAX_ACTIONS), ("results", MAX_RESULTS)):
        items = data.get(key)
        if not isinstance(items, dict) or len(items) <= limit:
            continue
        ordered = sorted(
            items.items(),
            key=lambda pair: pair[1].get("createdAt", "") if isinstance(pair[1], dict) else "",
        )
        data[key] = dict(ordered[-limit:])
