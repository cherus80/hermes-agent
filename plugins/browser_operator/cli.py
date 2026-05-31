"""CLI command for the browser_operator plugin."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from plugins.browser_operator import server, state


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="browser_operator_command")

    serve_p = subs.add_parser("serve", help="Start the Browser Operator HTTP companion")
    serve_p.add_argument("--host", default=server.DEFAULT_HOST)
    serve_p.add_argument("--port", type=int, default=server.DEFAULT_PORT)
    serve_p.add_argument(
        "--token",
        default=None,
        help="Bearer-like shared token. Defaults to HERMES_BROWSER_OPERATOR_TOKEN.",
    )
    serve_p.add_argument(
        "--insecure",
        action="store_true",
        help="Allow non-localhost listening without a token. Not recommended.",
    )

    subs.add_parser("status", help="Show sessions and latest snapshots")
    subs.add_parser("extension-path", help="Print the unpacked Chrome extension path")

    queue_p = subs.add_parser("queue", help="Queue a manual action for the extension")
    queue_p.add_argument("action", choices=("highlight", "click", "fill", "select", "navigate", "auth_probe"))
    queue_p.add_argument("--target", default="")
    queue_p.add_argument("--value", default=None)
    queue_p.add_argument("--session-id", default="default")
    queue_p.add_argument("--no-confirm", action="store_true")

    result_p = subs.add_parser("result", help="Show one action result")
    result_p.add_argument("action_id")

    subparser.set_defaults(func=browser_operator_command)


def browser_operator_command(args: argparse.Namespace) -> int:
    sub = getattr(args, "browser_operator_command", None)
    if not sub:
        print("usage: hermes browser-operator {serve,status,extension-path,queue,result}")
        return 2
    if sub == "serve":
        token = args.token if args.token is not None else os.getenv("HERMES_BROWSER_OPERATOR_TOKEN", "")
        host = str(args.host)
        if host not in ("127.0.0.1", "localhost", "::1") and not token and not args.insecure:
            print("Refusing to listen outside localhost without HERMES_BROWSER_OPERATOR_TOKEN.")
            print("Set a token or pass --insecure for a private test network.")
            return 2
        server.serve(host=host, port=int(args.port), token=token or "")
        return 0
    if sub == "status":
        return _cmd_status()
    if sub == "extension-path":
        print(state.extension_dir())
        return 0
    if sub == "queue":
        action = state.queue_action(
            {
                "sessionId": args.session_id,
                "type": args.action,
                "target": args.target,
                "value": args.value,
                "requiresUserConfirm": not bool(args.no_confirm),
            }
        )
        print(json.dumps(action, ensure_ascii=False, indent=2))
        return 0
    if sub == "result":
        result = state.get_result(args.action_id)
        print(json.dumps(result or {"ok": False, "status": "pending"}, ensure_ascii=False, indent=2))
        return 0
    print(f"unknown subcommand: {sub}")
    return 2


def _cmd_status() -> int:
    print("browser_operator status")
    print("-----------------------")
    print(f"state     : {state.state_path()}")
    print(f"audit     : {state.audit_path()}")
    print(f"extension : {state.extension_dir()}")
    sessions = state.list_sessions()
    print(f"sessions  : {len(sessions)}")
    for item in sessions:
        print(
            "  - {sessionId} tab={tabId} elements={elements} title={title!r} url={url}".format(
                **_fmt_item(item)
            )
        )
    return 0


def _fmt_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["title"] = (out.get("title") or "")[:80]
    out["url"] = (out.get("url") or "")[:140]
    return out

