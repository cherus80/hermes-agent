from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
from hermes_cli.model_switch import switch_model


def _jwt_payload(claims: dict) -> str:
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{payload}.sig"


def test_switch_model_blocks_expired_codex_subscription():
    expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    token_data = {
        "tokens": {
            "id_token": _jwt_payload(
                {
                    "https://api.openai.com/auth": {
                        "chatgpt_plan_type": "plus",
                        "chatgpt_subscription_active_until": expired,
                    }
                }
            )
        }
    }

    with (
        patch("hermes_cli.auth._read_codex_tokens", return_value=token_data),
        patch("hermes_cli.model_switch.fetch_account_usage", return_value=None),
        patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value={
                "provider": "openai-codex",
                "api_key": "codex-token",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_mode": "codex_responses",
            },
        ),
    ):
        result = switch_model(
            "gpt-5.4",
            current_provider="openrouter",
            current_model="openrouter/owl-alpha",
            explicit_provider="openai-codex",
        )

    assert result.success is False
    assert result.target_provider == "openai-codex"
    assert "subscription ended on" in result.error_message
    assert "Renew it or use another provider" in result.error_message


def test_switch_model_blocks_exhausted_codex_usage():
    usage = AccountUsageSnapshot(
        provider="openai-codex",
        source="usage_api",
        fetched_at=datetime.now(timezone.utc),
        plan="Plus",
        windows=(
            AccountUsageWindow(
                label="Session",
                used_percent=100.0,
                reset_at=datetime.now(timezone.utc) + timedelta(hours=3),
            ),
        ),
    )

    with (
        patch("hermes_cli.auth._read_codex_tokens", return_value={"tokens": {}}),
        patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value={
                "provider": "openai-codex",
                "api_key": "codex-token",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_mode": "codex_responses",
            },
        ),
        patch("hermes_cli.model_switch.fetch_account_usage", return_value=usage),
    ):
        result = switch_model(
            "gpt-5.4",
            current_provider="openrouter",
            current_model="openrouter/owl-alpha",
            explicit_provider="openai-codex",
        )

    assert result.success is False
    assert result.target_provider == "openai-codex"
    assert "100% used" in result.error_message
    assert "It resets at" in result.error_message


def test_switch_model_prefers_live_usage_over_stale_id_token():
    expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    token_data = {
        "tokens": {
            "id_token": _jwt_payload(
                {
                    "https://api.openai.com/auth": {
                        "chatgpt_plan_type": "plus",
                        "chatgpt_subscription_active_until": expired,
                    }
                }
            )
        }
    }
    usage = AccountUsageSnapshot(
        provider="openai-codex",
        source="usage_api",
        fetched_at=datetime.now(timezone.utc),
        plan="Plus",
        windows=(
            AccountUsageWindow(
                label="Session",
                used_percent=25.0,
                reset_at=datetime.now(timezone.utc) + timedelta(hours=3),
            ),
        ),
    )

    with (
        patch("hermes_cli.auth._read_codex_tokens", return_value=token_data),
        patch("hermes_cli.model_switch.fetch_account_usage", return_value=usage),
        patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value={
                "provider": "openai-codex",
                "api_key": "codex-token",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_mode": "codex_responses",
            },
        ),
    ):
        result = switch_model(
            "gpt-5.4",
            current_provider="openrouter",
            current_model="openrouter/owl-alpha",
            explicit_provider="openai-codex",
        )

    assert result.success is True
    assert result.target_provider == "openai-codex"
