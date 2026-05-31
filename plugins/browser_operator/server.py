"""Small HTTP polling server for the Hermes Browser Operator extension."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from plugins.browser_operator import state


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class BrowserOperatorServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, token: str = ""):
        super().__init__(server_address, RequestHandlerClass)
        self.token = token


class Handler(BaseHTTPRequestHandler):
    server: BrowserOperatorServer

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("HERMES_BROWSER_OPERATOR_DEBUG"):
            super().log_message(fmt, *args)

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hermes-Browser-Token")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 5_000_000))
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _authorized(self) -> bool:
        expected = getattr(self.server, "token", "") or ""
        if not expected:
            return True
        supplied = self.headers.get("X-Hermes-Browser-Token") or ""
        return supplied == expected

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return False

    def do_OPTIONS(self) -> None:
        self._send_json(HTTPStatus.OK, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/v1/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "hermes-browser-operator",
                    "sessions": state.list_sessions(),
                    "auth": "token" if getattr(self.server, "token", "") else "none",
                },
            )
            return
        if not self._require_auth():
            return
        if parsed.path == "/v1/actions/next":
            session_id = (qs.get("sessionId") or qs.get("session_id") or ["default"])[0]
            tab_id = (qs.get("tabId") or qs.get("tab_id") or ["active"])[0]
            action = state.next_action(session_id=session_id, tab_id=tab_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "action": action})
            return
        if parsed.path == "/v1/state":
            session_id = (qs.get("sessionId") or qs.get("session_id") or ["default"])[0]
            snapshot = state.latest_snapshot(session_id)
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "sessions": state.list_sessions(), "snapshot": snapshot},
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._require_auth():
            return
        body = self._read_json()
        if parsed.path == "/v1/snapshots":
            snapshot = state.upsert_snapshot(body)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "sessionId": snapshot["sessionId"],
                    "tabId": snapshot["tabId"],
                    "receivedAt": snapshot["receivedAt"],
                },
            )
            return
        if parsed.path == "/v1/actions/result":
            result = state.record_result(body)
            self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
            return
        if parsed.path == "/v1/actions/queue":
            queued = state.queue_action(body)
            self._send_json(HTTPStatus.OK, {"ok": True, "action": queued})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, token: str = "") -> None:
    httpd = BrowserOperatorServer((host, int(port)), Handler, token=token)
    print(f"browser-operator: listening on http://{host}:{port}")
    print(f"browser-operator: extension path {state.extension_dir()}")
    if token:
        print("browser-operator: token auth enabled")
    else:
        print("browser-operator: token auth disabled; use localhost only")
    httpd.serve_forever()

