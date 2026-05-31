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
            }
        },
        "additionalProperties": False,
    },
}


BROWSER_OPERATOR_FIND_ELEMENTS_SCHEMA: Dict[str, Any] = {
    "name": "browser_operator_find_elements",
    "description": (
        "Find likely visible page elements for a natural-language target such "
        "as 'comment field', 'publish button', or 'account menu'. Returns "
        "ranked candidates from the latest extension snapshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Natural language UI target."},
            "session_id": {"type": "string", "description": "Defaults to 'default'."},
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
                "enum": ["highlight", "click", "fill", "select", "navigate", "auth_probe"],
            },
            "target": {
                "type": "string",
                "description": "Natural-language target, e.g. 'title field' or 'publish button'.",
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
            "requires_user_confirm": {
                "type": "boolean",
                "description": (
                    "Whether the extension must ask before running. Defaults "
                    "to true for click/navigate and false for highlight/fill/select."
                ),
            },
        },
        "required": ["action"],
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
            "expected_account": {
                "type": "string",
                "description": "Optional account/channel/user hint to look for in page text.",
            },
        },
        "additionalProperties": False,
    },
}


def handle_latest_snapshot(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    snapshot = state.latest_snapshot(session_id)
    if not snapshot:
        return _json(
            {
                "ok": False,
                "error": "No browser snapshot received yet.",
                "hint": "Start `hermes browser-operator serve`, load the extension, and open a page.",
                "sessions": state.list_sessions(),
            }
        )
    return _json({"ok": True, "snapshot": resolver.summarize_snapshot(snapshot)})


def handle_find_elements(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = _args(args)
    session_id = str(args.get("session_id") or "default")
    target = str(args.get("target") or "").strip()
    limit = int(args.get("limit") or 8)
    snapshot = state.latest_snapshot(session_id)
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
    risky = action_type in {"click", "navigate"}
    if "requires_user_confirm" in args:
        requires_confirm = bool(args.get("requires_user_confirm"))
    else:
        requires_confirm = risky
    queued = state.queue_action(
        {
            "sessionId": session_id,
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
    expected = str(args.get("expected_account") or "").strip().lower()
    snapshot = state.latest_snapshot(session_id)
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
