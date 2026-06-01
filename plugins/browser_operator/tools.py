"""Agent-facing tools for the browser_operator plugin."""

from __future__ import annotations

import json
from typing import Any, Dict

from plugins.browser_operator import resolver, state


def _json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def check_browser_operator_requirements() -> bool:
    return True


def _args(args: Dict[str, Any] | None) -> Dict[str, Any]:
    return args if isinstance(args, dict) else {}


def _snapshot_for(session_id: str, tab_id: str = "") -> Dict[str, Any] | None:
    return state.get_tab_snapshot(session_id, tab_id) if tab_id else state.latest_snapshot(session_id)


def _target_tab_id(args: Dict[str, Any], session_id: str) -> str:
    tab_id = str(args.get("tab_id") or args.get("tabId") or "").strip()
    if tab_id:
        return tab_id
    snapshot = state.latest_snapshot(session_id)
    if isinstance(snapshot, dict) and snapshot.get("tabId"):
        return str(snapshot.get("tabId"))
    return "active"


BROWSER_OPERATOR_LIST_TABS_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_list_tabs",
    "description": (
        "List browser tabs currently known to Hermes Browser Operator. Use this "
        "when the user changes tabs or when you need to target a specific open tab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "query": {"type": "string", "description": "Optional URL/title/text filter."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_FIND_TAB_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_find_tab",
    "description": (
        "Find the most likely open browser tab by URL, title, or page text. "
        "Returns tab_id values that can be passed to other browser_operator tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Target tab hint, e.g. 'Threads', 'YouTube Studio', or a URL."},
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_LATEST_SNAPSHOT_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_latest_snapshot",
    "description": (
        "Return the latest page snapshot received from the Hermes Browser "
        "Operator extension. Use this before deciding what browser action to "
        "queue. Does not expose passwords or input values from password fields."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Browser operator session id. Defaults to 'default'.",
            },
            "tab_id": {
                "type": "string",
                "description": "Optional specific tab id from browser_operator_list_tabs/find_tab.",
            },
        },
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_FIND_ELEMENTS_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_find_elements",
    "description": (
        "Find likely visible page elements for a natural-language target such "
        "as 'comment field', 'publish button', or 'account menu'. Returns "
        "ranked candidates from the latest extension snapshot, including stable "
        "selectors when available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Natural language UI target."},
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "tab_id": {"type": "string", "description": "Optional specific tab id."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        },
        "required": ["target"],
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_QUEUE_ACTION_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_queue_action",
    "description": (
        "Queue a controlled browser action for the extension. The extension "
        "resolves the target again on the live page, highlights it, and asks "
        "the user to confirm risky actions such as click, submit, and navigate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "highlight",
                    "click",
                    "fill",
                    "select",
                    "scroll",
                    "navigate",
                    "back",
                    "forward",
                    "reload",
                    "auth_probe",
                    "focus_tab",
                ],
            },
            "target": {
                "type": "string",
                "description": "Natural-language target, e.g. 'title field' or 'publish button'. You may also pass selector:<css> or css=<css> from a candidate selector.",
            },
            "value": {
                "type": "string",
                "description": "Text/value for fill, select, or navigate actions.",
            },
            "reason": {
                "type": "string",
                "description": "Short user-facing reason shown by the extension before confirmation.",
            },
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "tab_id": {"type": "string", "description": "Specific tab id from browser_operator_list_tabs/find_tab."},
            "requires_user_confirm": {
                "type": "boolean",
                "description": (
                    "Whether the extension must ask before running. Defaults "
                    "to true for click/navigate and false for highlight/fill/select. "
                    "The user's extension approval-mode setting can still force "
                    "confirmation or allow auto-run."
                ),
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_SCROLL_PAGE_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_scroll_page",
    "description": (
        "Scroll the current browser page through the Hermes Browser Operator "
        "extension, then refresh the page snapshot. Use this when content is "
        "below/above the visible viewport."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["down", "up", "top", "bottom"],
                "description": "Scroll direction. Defaults to down.",
            },
            "amount": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10000,
                "description": "Pixels or pages to scroll. Defaults to 1 page.",
            },
            "unit": {
                "type": "string",
                "enum": ["pages", "pixels"],
                "description": "Whether amount is pages or pixels. Defaults to pages.",
            },
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "tab_id": {"type": "string", "description": "Specific tab id to scroll."},
            "reason": {
                "type": "string",
                "description": "Optional short reason for the audit log.",
            },
        },
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_NAVIGATE_PAGE_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_navigate_page",
    "description": (
        "Control browser page navigation through the extension: navigate to a "
        "URL, go back, go forward, or reload. URL navigation asks the user for "
        "confirmation by default."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "back", "forward", "reload"],
                "description": "Navigation action. Defaults to reload.",
            },
            "url": {
                "type": "string",
                "description": "Destination URL for action=navigate.",
            },
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "tab_id": {"type": "string", "description": "Specific tab id to navigate."},
            "requires_user_confirm": {
                "type": "boolean",
                "description": "Override confirmation. URL navigation defaults to true.",
            },
            "reason": {
                "type": "string",
                "description": "Short user-facing reason shown before confirmed navigation.",
            },
        },
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_ACTION_RESULT_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_action_result",
    "description": "Return the result for a queued browser action id.",
    "parameters": {
        "type": "object",
        "properties": {
            "action_id": {"type": "string"},
        },
        "required": ["action_id"],
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_AUTH_STATUS_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_auth_status",
    "description": (
        "Estimate whether the current page appears signed in by inspecting the "
        "latest page snapshot. This is a heuristic preflight; if uncertain, "
        "ask the user to verify the account in the browser."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
            "tab_id": {"type": "string", "description": "Optional specific tab id."},
            "expected_account": {
                "type": "string",
                "description": "Optional account/channel/user hint to look for in page text.",
            },
        },
        "additionalProperties": False,
    },
}


def handle_list_tabs(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    query = str(args.get("query") or "")
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    return _json({"ok": True, "tabs": state.list_tabs(session_id=session_id, query=query, limit=limit)})


def handle_find_tab(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    query = str(args.get("query") or "").strip()
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    matches = state.find_tabs(session_id=session_id, query=query, limit=limit)
    return _json(
        {
            "ok": True,
            "query": query,
            "matches": matches,
            "bestTabId": matches[0]["tabId"] if matches else None,
            "needsUserChoice": not matches or float(matches[0].get("score") or 0) < 2.0,
        }
    )


def handle_latest_snapshot(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    tab_id = str(args.get("tab_id") or "")
    snapshot = _snapshot_for(session_id, tab_id)
    if not snapshot:
        return _json(
            {
                "ok": False,
                "error": "No browser snapshot received yet.",
                "hint": "Start `hermes browser-operator serve`, load the extension, and open a page.",
                "sessions": state.list_sessions(),
                "tabs": state.list_tabs(session_id=session_id, limit=50),
            }
        )
    return _json({"ok": True, "snapshot": resolver.summarize_snapshot(snapshot)})


def handle_find_elements(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    tab_id = str(args.get("tab_id") or "")
    target = str(args.get("target") or "").strip()
    limit = int(args.get("limit") or 8)
    snapshot = _snapshot_for(session_id, tab_id)
    if not snapshot:
        return _json({"ok": False, "error": "No browser snapshot received yet."})
    candidates = resolver.find_candidates(snapshot, target, limit)
    return _json(
        {
            "ok": True,
            "target": target,
            "snapshot": resolver.summarize_snapshot(snapshot),
            "candidates": candidates,
            "confidence": candidates[0]["score"] if candidates else 0,
            "needsUserChoice": not candidates or candidates[0]["score"] < 2.0,
        }
    )


def handle_queue_action(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    action_type = str(args.get("action") or "highlight")
    session_id = str(args.get("session_id") or "default")
    tab_id = _target_tab_id(args, session_id)
    risky = action_type in {"click", "navigate"}
    if "requires_user_confirm" in args:
        requires_confirm = bool(args.get("requires_user_confirm"))
    else:
        requires_confirm = risky
    queued = state.queue_action(
        {
            "sessionId": session_id,
            "tabId": tab_id,
            "type": action_type,
            "target": str(args.get("target") or ""),
            "value": args.get("value"),
            "reason": str(args.get("reason") or ""),
            "requiresUserConfirm": requires_confirm,
        }
    )
    return _json(
        {
            "ok": True,
            "action": queued,
            "hint": "Poll browser_operator_action_result with this action id after the extension executes it.",
        }
    )


def handle_scroll_page(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    direction = str(args.get("direction") or "down").lower()
    if direction not in {"down", "up", "top", "bottom"}:
        direction = "down"
    unit = str(args.get("unit") or "pages").lower()
    if unit not in {"pages", "pixels"}:
        unit = "pages"
    try:
        amount = int(args.get("amount") or 1)
    except (TypeError, ValueError):
        amount = 1
    amount = max(1, min(amount, 10000))
    session_id = str(args.get("session_id") or "default")
    tab_id = _target_tab_id(args, session_id)
    queued = state.queue_action(
        {
            "sessionId": session_id,
            "tabId": tab_id,
            "type": "scroll",
            "target": direction,
            "value": {"direction": direction, "amount": amount, "unit": unit},
            "reason": str(args.get("reason") or f"scroll {direction}"),
            "requiresUserConfirm": False,
        }
    )
    return _json(
        {
            "ok": True,
            "action": queued,
            "hint": "The extension will scroll the current page and refresh the snapshot.",
        }
    )


def handle_navigate_page(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    action_type = str(args.get("action") or "reload").lower()
    if action_type not in {"navigate", "back", "forward", "reload"}:
        action_type = "reload"
    url = str(args.get("url") or "").strip()
    if action_type == "navigate" and not url:
        return _json({"ok": False, "error": "url is required for action=navigate"})
    session_id = str(args.get("session_id") or "default")
    tab_id = _target_tab_id(args, session_id)
    if "requires_user_confirm" in args:
        requires_confirm = bool(args.get("requires_user_confirm"))
    else:
        requires_confirm = action_type == "navigate"
    queued = state.queue_action(
        {
            "sessionId": session_id,
            "tabId": tab_id,
            "type": action_type,
            "target": url if action_type == "navigate" else action_type,
            "value": url if action_type == "navigate" else "",
            "reason": str(args.get("reason") or action_type),
            "requiresUserConfirm": requires_confirm,
        }
    )
    return _json(
        {
            "ok": True,
            "action": queued,
            "hint": "The extension will perform the navigation action, then refresh the snapshot when possible.",
        }
    )


def handle_action_result(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    action_id = str(args.get("action_id") or "")
    result = state.get_result(action_id)
    if not result:
        return _json({"ok": False, "status": "pending", "actionId": action_id})
    return _json({"ok": True, "result": result})


def handle_auth_status(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    tab_id = str(args.get("tab_id") or "")
    expected = str(args.get("expected_account") or "").strip().lower()
    snapshot = _snapshot_for(session_id, tab_id)
    if not snapshot:
        return _json({"ok": False, "error": "No browser snapshot received yet."})

    signals = snapshot.get("authSignals") or {}
    text = ((snapshot.get("title") or "") + "\n" + (snapshot.get("text") or "")).lower()
    login_hits = int(signals.get("loginElements") or 0)
    password_inputs = int(signals.get("passwordInputs") or 0)
    account_hits = int(signals.get("accountElements") or 0)
    expected_found = bool(expected and expected in text)

    status = "unknown"
    reasons = []
    if password_inputs:
        status = "unauthorized"
        reasons.append("password input is visible")
    if login_hits and not account_hits:
        status = "unauthorized"
        reasons.append("login/sign-in controls are visible")
    if account_hits and not password_inputs:
        status = "authenticated"
        reasons.append("account/avatar controls are visible")
    if expected:
        if expected_found:
            status = "authenticated"
            reasons.append("expected account hint found on page")
        else:
            reasons.append("expected account hint was not found")

    return _json(
        {
            "ok": True,
            "status": status,
            "reasons": reasons,
            "authSignals": signals,
            "snapshot": resolver.summarize_snapshot(snapshot),
            "nextStep": (
                "Ask the user to sign in or verify the account in the browser."
                if status != "authenticated"
                else "Account preflight looks usable."
            ),
        }
    )
