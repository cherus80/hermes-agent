import pytest
import json

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageType
from gateway.platforms.vk import VKAdapter


@pytest.mark.asyncio
async def test_vk_poll_once_routes_recent_message_to_gateway(tmp_path):
    adapter = VKAdapter(PlatformConfig(enabled=True, token="vk-token"))
    adapter.state_path = tmp_path / "vk_state.json"
    adapter._seen_ids = set()

    api_calls = []

    async def fake_vk_api(method, payload):
        api_calls.append((method, payload))
        if method == "messages.getConversations":
            return {
                "items": [
                    {
                        "last_message": {
                            "id": 42,
                            "peer_id": 123456789,
                            "from_id": 987654321,
                            "text": "hello from vk",
                            "out": 0,
                        }
                    }
                ]
            }
        if method == "messages.markAsRead":
            return 1
        raise AssertionError(f"unexpected VK API method: {method}")

    events = []

    async def fake_handle_message(event):
        events.append(event)

    adapter._vk_api = fake_vk_api
    adapter.handle_message = fake_handle_message

    await adapter._poll_once()

    assert len(events) == 1
    event = events[0]
    assert event.text == "hello from vk"
    assert event.message_type == MessageType.TEXT
    assert event.source.platform == Platform.VK
    assert event.source.chat_id == "123456789"
    assert event.source.user_id == "987654321"
    assert (
        "messages.getConversations",
        {"count": str(adapter.batch_size), "filter": "all"},
    ) in api_calls
    assert ("messages.markAsRead", {"peer_id": "123456789"}) in api_calls


@pytest.mark.asyncio
async def test_vk_poll_once_routes_admin_panel_message(tmp_path):
    adapter = VKAdapter(PlatformConfig(enabled=True, token="vk-token"))
    adapter.state_path = tmp_path / "vk_state.json"
    adapter._seen_ids = set()

    async def fake_vk_api(method, payload):
        if method == "messages.getConversations":
            return {
                "items": [
                    {
                        "last_message": {
                            "id": 8,
                            "peer_id": 327943125,
                            "from_id": -239543149,
                            "admin_author_id": 327943125,
                            "text": "hello from community admin panel",
                            "out": 1,
                        }
                    }
                ]
            }
        if method == "messages.markAsRead":
            return 1
        raise AssertionError(f"unexpected VK API method: {method}")

    events = []

    async def fake_handle_message(event):
        events.append(event)

    adapter._vk_api = fake_vk_api
    adapter.handle_message = fake_handle_message

    await adapter._poll_once()

    assert len(events) == 1
    event = events[0]
    assert event.text == "hello from community admin panel"
    assert event.source.chat_id == "327943125"
    assert event.source.user_id == "327943125"
    assert event.raw_message["admin_author_id"] == 327943125


@pytest.mark.asyncio
async def test_vk_poll_once_ignores_own_outgoing_message(tmp_path):
    adapter = VKAdapter(PlatformConfig(enabled=True, token="vk-token"))
    adapter.state_path = tmp_path / "vk_state.json"
    adapter._seen_ids = set()

    async def fake_vk_api(method, payload):
        if method == "messages.getConversations":
            return {
                "items": [
                    {
                        "last_message": {
                            "id": 9,
                            "peer_id": 327943125,
                            "from_id": -239543149,
                            "text": "automatic reply",
                            "out": 1,
                        }
                    }
                ]
            }
        raise AssertionError(f"unexpected VK API method: {method}")

    events = []

    async def fake_handle_message(event):
        events.append(event)

    adapter._vk_api = fake_vk_api
    adapter.handle_message = fake_handle_message

    await adapter._poll_once()

    assert events == []


def test_vk_parse_approval_code_accepts_bridge_formats():
    assert VKAdapter.parse_approval_code("/approve secret-code") == "secret-code"
    assert VKAdapter.parse_approval_code("код 12345") == "12345"
    assert VKAdapter.parse_approval_code("hello") is None


@pytest.mark.asyncio
async def test_vk_poll_once_routes_button_payload_command(tmp_path):
    adapter = VKAdapter(PlatformConfig(enabled=True, token="vk-token"))
    adapter.state_path = tmp_path / "vk_state.json"
    adapter._seen_ids = set()

    async def fake_vk_api(method, payload):
        if method == "messages.getConversations":
            return {
                "items": [
                    {
                        "last_message": {
                            "id": 10,
                            "peer_id": 327943125,
                            "from_id": 327943125,
                            "text": "Разрешить",
                            "payload": json.dumps({"command": "/approve"}),
                            "out": 0,
                        }
                    }
                ]
            }
        if method == "messages.markAsRead":
            return 1
        raise AssertionError(f"unexpected VK API method: {method}")

    events = []

    async def fake_handle_message(event):
        events.append(event)

    adapter._vk_api = fake_vk_api
    adapter.handle_message = fake_handle_message

    await adapter._poll_once()

    assert len(events) == 1
    assert events[0].text == "/approve"
    assert events[0].message_type == MessageType.COMMAND


@pytest.mark.asyncio
async def test_vk_send_attaches_command_keyboard():
    adapter = VKAdapter(PlatformConfig(enabled=True, token="vk-token"))
    calls = []

    async def fake_vk_api(method, payload):
        calls.append((method, payload))
        return 123

    adapter._vk_api = fake_vk_api

    result = await adapter.send("327943125", "готово")

    assert result.success is True
    method, payload = calls[-1]
    assert method == "messages.send"
    keyboard = json.loads(payload["keyboard"])
    assert keyboard["inline"] is False
    assert keyboard["buttons"][0][0]["action"]["label"] == "Команды"
    assert json.loads(keyboard["buttons"][0][0]["action"]["payload"]) == {
        "command": "/commands"
    }


@pytest.mark.asyncio
async def test_vk_send_exec_approval_uses_command_keyboard():
    adapter = VKAdapter(PlatformConfig(enabled=True, token="vk-token"))
    calls = []

    async def fake_vk_api(method, payload):
        calls.append((method, payload))
        return 456

    adapter._vk_api = fake_vk_api

    result = await adapter.send_exec_approval(
        chat_id="327943125",
        command="execute_code print('hello')",
        session_key="agent:main:vk:dm:327943125",
        description="execute_code script execution",
    )

    assert result.success is True
    method, payload = calls[-1]
    assert method == "messages.send"
    assert "Нужно подтверждение" in payload["message"]
    keyboard = json.loads(payload["keyboard"])
    assert keyboard["inline"] is False
    commands = [
        json.loads(button["action"]["payload"])["command"]
        for row in keyboard["buttons"]
        for button in row
    ]
    assert commands == ["/approve", "/approve session", "/approve always", "/deny"]
