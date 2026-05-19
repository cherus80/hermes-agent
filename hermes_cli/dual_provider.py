"""Session-scoped helpers for OpenRouter <-> GrsAI provider selection."""

from __future__ import annotations

import os
from typing import Any, Optional

from hermes_cli.auth import PROVIDER_REGISTRY, has_usable_secret
from hermes_cli.model_normalize import normalize_model_for_provider
from hermes_cli.models import _PROVIDER_MODELS

DUAL_PROVIDER_PRIMARY = "openrouter"
DUAL_PROVIDER_SECONDARY = "grsai"
DUAL_PROVIDER_IDS = (DUAL_PROVIDER_PRIMARY, DUAL_PROVIDER_SECONDARY)

_CHOICE_ALIASES = {
    "1": "openrouter",
    "2": "grsai",
    "openrouter": "openrouter",
    "open-router": "openrouter",
    "or": "openrouter",
    "grsai": "grsai",
    "grs": "grsai",
    "grs-ai": "grsai",
}


def dual_provider_is_configured(provider: str) -> bool:
    """Return whether a provider from the dual-provider pair is configured."""
    normalized = (provider or "").strip().lower()
    if normalized == "openrouter":
        return has_usable_secret(os.getenv("OPENROUTER_API_KEY", ""))

    pconfig = PROVIDER_REGISTRY.get(normalized)
    if not pconfig:
        return False
    return any(has_usable_secret(os.getenv(var, "")) for var in pconfig.api_key_env_vars)


def dual_provider_prompt_enabled() -> bool:
    """Prompt only when both providers are available for this installation."""
    return all(dual_provider_is_configured(provider) for provider in DUAL_PROVIDER_IDS)


def dual_provider_default_provider() -> str:
    """Return the default provider for new dual-provider sessions."""
    return DUAL_PROVIDER_PRIMARY


def normalize_dual_provider_choice(raw: Optional[str]) -> Optional[str]:
    """Parse a user-facing provider choice into a canonical provider id."""
    normalized = (raw or "").strip().lower()
    if not normalized:
        return None
    return _CHOICE_ALIASES.get(normalized)


def dual_provider_prompt_text(*, retry: bool = False) -> str:
    prefix = "Не понял выбор." if retry else "Перед началом новой сессии выбери провайдера."
    return (
        f"{prefix}\n"
        "Ответь одним словом: `openrouter` или `grsai`.\n"
        "Первый запрос я сохраню и продолжу сразу после выбора."
    )


def dual_provider_cli_prompt(default_provider: str = DUAL_PROVIDER_PRIMARY) -> tuple[str, str]:
    default_label = "OpenRouter" if default_provider == DUAL_PROVIDER_PRIMARY else "GrsAI"
    default_value = default_provider if default_provider in DUAL_PROVIDER_IDS else DUAL_PROVIDER_PRIMARY
    return (
        "\n"
        "Выбери провайдера для этой сессии:\n"
        "  1. OpenRouter\n"
        "  2. GrsAI\n"
        f"Провайдер [{default_label}]: "
    ), default_value


def dual_provider_supports_model(provider: str, model: Optional[str]) -> bool:
    """Return whether the provider can plausibly serve the requested model."""
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip()
    if not normalized_provider or not normalized_model:
        return False
    if normalized_provider == "openrouter":
        return True

    try:
        provider_model = normalize_model_for_provider(normalized_model, normalized_provider)
    except Exception:
        provider_model = normalized_model

    supported = {
        str(entry).strip().lower()
        for entry in _PROVIDER_MODELS.get(normalized_provider, [])
        if str(entry).strip()
    }
    return str(provider_model).strip().lower() in supported


def _normalize_fallback_entries(configured: Any) -> list[dict[str, Any]]:
    if isinstance(configured, list):
        return [
            entry for entry in configured
            if isinstance(entry, dict) and entry.get("provider") and entry.get("model")
        ]
    if isinstance(configured, dict) and configured.get("provider") and configured.get("model"):
        return [configured]
    return []


def build_dual_provider_fallbacks(
    *,
    primary_provider: Optional[str],
    model: Optional[str],
    configured_fallbacks: Any = None,
) -> list[dict[str, Any]]:
    """Build a fallback chain with the sister provider prepended when possible."""
    normalized_primary = (primary_provider or "").strip().lower()
    normalized_model = (model or "").strip()
    configured = _normalize_fallback_entries(configured_fallbacks)

    if normalized_primary not in DUAL_PROVIDER_IDS or not normalized_model:
        return configured

    sister = DUAL_PROVIDER_SECONDARY if normalized_primary == DUAL_PROVIDER_PRIMARY else DUAL_PROVIDER_PRIMARY
    if not dual_provider_is_configured(sister):
        return configured

    if not dual_provider_supports_model(sister, normalized_model):
        return configured

    sister_entry = {"provider": sister, "model": normalized_model}
    chain = [sister_entry]
    for entry in configured:
        provider = str(entry.get("provider") or "").strip().lower()
        model_name = str(entry.get("model") or "").strip()
        if provider == sister and model_name == normalized_model:
            continue
        chain.append(entry)
    return chain
