import pytest

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
