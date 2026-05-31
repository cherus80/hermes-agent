"""browser_operator plugin registration."""

from __future__ import annotations

from pathlib import Path

from plugins.browser_operator.cli import browser_operator_command, register_cli
from plugins.browser_operator.tools import (
    BROWSER_OPERATOR_ACTION_RESULT_SCHEMA,
    BROWSER_OPERATOR_AUTH_STATUS_SCHEMA,
    BROWSER_OPERATOR_FIND_TAB_SCHEMA,
    BROWSER_OPERATOR_FIND_ELEMENTS_SCHEMA,
    BROWSER_OPERATOR_LATEST_SNAPSHOT_SCHEMA,
    BROWSER_OPERATOR_LIST_TABS_SCHEMA,
    BROWSER_OPERATOR_NAVIGATE_PAGE_SCHEMA,
    BROWSER_OPERATOR_QUEUE_ACTION_SCHEMA,
    BROWSER_OPERATOR_SCROLL_PAGE_SCHEMA,
    check_browser_operator_requirements,
    handle_action_result,
    handle_auth_status,
    handle_find_tab,
    handle_find_elements,
    handle_latest_snapshot,
    handle_list_tabs,
    handle_navigate_page,
    handle_queue_action,
    handle_scroll_page,
)


_TOOLS = (
    ("browser_operator_list_tabs", BROWSER_OPERATOR_LIST_TABS_SCHEMA, handle_list_tabs, "BR"),
    ("browser_operator_find_tab", BROWSER_OPERATOR_FIND_TAB_SCHEMA, handle_find_tab, "BR"),
    ("browser_operator_latest_snapshot", BROWSER_OPERATOR_LATEST_SNAPSHOT_SCHEMA, handle_latest_snapshot, "BR"),
    ("browser_operator_find_elements", BROWSER_OPERATOR_FIND_ELEMENTS_SCHEMA, handle_find_elements, "BR"),
    ("browser_operator_scroll_page", BROWSER_OPERATOR_SCROLL_PAGE_SCHEMA, handle_scroll_page, "BR"),
    ("browser_operator_navigate_page", BROWSER_OPERATOR_NAVIGATE_PAGE_SCHEMA, handle_navigate_page, "BR"),
    ("browser_operator_queue_action", BROWSER_OPERATOR_QUEUE_ACTION_SCHEMA, handle_queue_action, "BR"),
    ("browser_operator_action_result", BROWSER_OPERATOR_ACTION_RESULT_SCHEMA, handle_action_result, "BR"),
    ("browser_operator_auth_status", BROWSER_OPERATOR_AUTH_STATUS_SCHEMA, handle_auth_status, "BR"),
)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="browser_operator",
            schema=schema,
            handler=handler,
            check_fn=check_browser_operator_requirements,
            emoji=emoji,
        )

    ctx.register_cli_command(
        name="browser-operator",
        help="Hermes Browser Operator companion extension and local HTTP bridge",
        setup_fn=register_cli,
        handler_fn=browser_operator_command,
        description="Run and inspect the Hermes Browser Operator companion.",
    )

    skill_path = Path(__file__).resolve().parent / "SKILL.md"
    if skill_path.exists():
        ctx.register_skill(
            name="browser-operator",
            path=skill_path,
            description="Use the Hermes Browser Operator extension safely.",
        )
