from hermes_cli.auth import resolve_provider
from hermes_cli.dual_provider import (
    build_dual_provider_fallbacks,
    dual_provider_prompt_enabled,
    normalize_dual_provider_choice,
)
from hermes_cli.model_normalize import normalize_model_for_provider
from hermes_cli.runtime_provider import resolve_runtime_provider


def test_resolve_provider_prefers_grsai_when_only_grsai_key_present(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GRSAI_API_KEY", "grs-test-key")

    assert resolve_provider("auto") == "grsai"


def test_resolve_runtime_provider_for_grsai(monkeypatch):
    monkeypatch.setenv("GRSAI_API_KEY", "grs-test-key")
    monkeypatch.delenv("GRSAI_BASE_URL", raising=False)

    runtime = resolve_runtime_provider(requested="grsai")

    assert runtime["provider"] == "grsai"
    assert runtime["api_key"] == "grs-test-key"
    assert runtime["base_url"] == "https://api.grsai.com/v1"
    assert runtime["api_mode"] == "chat_completions"


def test_normalize_model_for_grsai_strips_aggregator_prefix():
    assert normalize_model_for_provider("openai/gpt-5.5", "grsai") == "gpt-5.5"
    assert normalize_model_for_provider("anthropic/claude-sonnet-4.5", "grsai") == "claude-sonnet-4.5"


def test_dual_provider_helpers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("GRSAI_API_KEY", "grs-test-key")

    assert dual_provider_prompt_enabled() is True
    assert normalize_dual_provider_choice("2") == "grsai"
    assert normalize_dual_provider_choice("openrouter") == "openrouter"

    chain = build_dual_provider_fallbacks(
        primary_provider="openrouter",
        model="openai/gpt-5.4",
        configured_fallbacks=[],
    )

    assert chain == [{"provider": "grsai", "model": "openai/gpt-5.4"}]
