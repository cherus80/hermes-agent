#!/usr/bin/env python3
"""
Image Generation Tools Module

Provides image generation via FAL.ai. Multiple FAL models are supported and
selectable via ``hermes tools`` → Image Generation; the active model is
persisted to ``image_gen.model`` in ``config.yaml``.

Architecture:
- ``FAL_MODELS`` is a catalog of supported models with per-model metadata
  (size-style family, defaults, ``supports`` whitelist, upscaler flag).
- ``_build_fal_payload()`` translates the agent's unified inputs (prompt +
  aspect_ratio) into the model-specific payload and filters to the
  ``supports`` whitelist so models never receive rejected keys.
- Upscaling via FAL's Clarity Upscaler is gated per-model via the ``upscale``
  flag — on for FLUX 2 Pro (backward-compat), off for all faster/newer models
  where upscaling would either hurt latency or add marginal quality.

Pricing shown in UI strings is as-of the initial commit; we accept drift and
update when it's noticed.
"""

import json
import logging
import os
import datetime
import time
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode
import urllib.request
import httpx
from tools.debug_helpers import DebugSession
from tools.managed_tool_gateway import resolve_managed_tool_gateway
try:
    from tools.tool_backend_helpers import (
        fal_key_is_configured,
        managed_nous_tools_enabled,
        prefers_gateway,
    )
except Exception:  # pragma: no cover - compatibility for isolated tests
    def fal_key_is_configured() -> bool:
        return bool(str(os.getenv("FAL_KEY", "")).strip())

    def managed_nous_tools_enabled() -> bool:
        return False

    def prefers_gateway(_tool_name: str) -> bool:
        return False

try:
    import fal_client
except ImportError:  # pragma: no cover - exercised in environment-specific setups
    fal_client = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FAL model catalog
# ---------------------------------------------------------------------------
#
# Each entry declares how to translate our unified inputs into the model's
# native payload shape. Size specification falls into three families:
#
#   "image_size_preset" — preset enum ("square_hd", "landscape_16_9", ...)
#                          used by the flux family, z-image, qwen, recraft,
#                          ideogram.
#   "aspect_ratio"      — aspect ratio enum ("16:9", "1:1", ...) used by
#                          nano-banana (Gemini).
#   "gpt_literal"       — literal dimension strings ("1024x1024", etc.)
#                          used by gpt-image-1.5.
#
# ``supports`` is a whitelist of keys allowed in the outgoing payload — any
# key outside this set is stripped before submission so models never receive
# rejected parameters (each FAL model rejects unknown keys differently).
#
# ``upscale`` controls whether to chain Clarity Upscaler after generation.

FAL_MODELS: Dict[str, Dict[str, Any]] = {
    "fal-ai/flux-2/klein/9b": {
        "display": "FLUX 2 Klein 9B",
        "speed": "<1s",
        "strengths": "Fast, crisp text",
        "price": "$0.006/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 4,
            "output_format": "png",
            "enable_safety_checker": False,
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "seed",
            "output_format", "enable_safety_checker",
        },
        "upscale": False,
    },
    "fal-ai/flux-2-pro": {
        "display": "FLUX 2 Pro",
        "speed": "~6s",
        "strengths": "Studio photorealism",
        "price": "$0.03/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 50,
            "guidance_scale": 4.5,
            "num_images": 1,
            "output_format": "png",
            "enable_safety_checker": False,
            "safety_tolerance": "5",
            "sync_mode": True,
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "guidance_scale",
            "num_images", "output_format", "enable_safety_checker",
            "safety_tolerance", "sync_mode", "seed",
        },
        "upscale": True,   # Backward-compat: current default behavior.
    },
    "fal-ai/z-image/turbo": {
        "display": "Z-Image Turbo",
        "speed": "~2s",
        "strengths": "Bilingual EN/CN, 6B",
        "price": "$0.005/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 8,
            "num_images": 1,
            "output_format": "png",
            "enable_safety_checker": False,
            "enable_prompt_expansion": False,  # avoid the extra per-request charge
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "num_images",
            "seed", "output_format", "enable_safety_checker",
            "enable_prompt_expansion",
        },
        "upscale": False,
    },
    "fal-ai/nano-banana-pro": {
        "display": "Nano Banana Pro (Gemini 3 Pro Image)",
        "speed": "~8s",
        "strengths": "Gemini 3 Pro, reasoning depth, text rendering",
        "price": "$0.15/image (1K)",
        "size_style": "aspect_ratio",
        "sizes": {
            "landscape": "16:9",
            "square": "1:1",
            "portrait": "9:16",
        },
        "defaults": {
            "num_images": 1,
            "output_format": "png",
            "safety_tolerance": "5",
            # "1K" is the cheapest tier; 4K doubles the per-image cost.
            # Users on Nous Subscription should stay at 1K for predictable billing.
            "resolution": "1K",
        },
        "supports": {
            "prompt", "aspect_ratio", "num_images", "output_format",
            "safety_tolerance", "seed", "sync_mode", "resolution",
            "enable_web_search", "limit_generations",
        },
        "upscale": False,
    },
    "fal-ai/gpt-image-1.5": {
        "display": "GPT Image 1.5",
        "speed": "~15s",
        "strengths": "Prompt adherence",
        "price": "$0.034/image",
        "size_style": "gpt_literal",
        "sizes": {
            "landscape": "1536x1024",
            "square": "1024x1024",
            "portrait": "1024x1536",
        },
        "defaults": {
            # Quality is pinned to medium to keep portal billing predictable
            # across all users (low is too rough, high is 4-6x more expensive).
            "quality": "medium",
            "num_images": 1,
            "output_format": "png",
        },
        "supports": {
            "prompt", "image_size", "quality", "num_images", "output_format",
            "background", "sync_mode",
        },
        "upscale": False,
    },
    "fal-ai/gpt-image-2": {
        "display": "GPT Image 2",
        "speed": "~20s",
        "strengths": "SOTA text rendering + CJK, world-aware photorealism",
        "price": "$0.04–0.06/image",
        # GPT Image 2 uses FAL's standard preset enum (unlike 1.5's literal
        # dimensions). We map to the 4:3 variants — the 16:9 presets
        # (1024x576) fall below GPT-Image-2's 655,360 min-pixel requirement
        # and would be rejected. 4:3 keeps us above the minimum on all
        # three aspect ratios.
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_4_3",   # 1024x768
            "square": "square_hd",            # 1024x1024
            "portrait": "portrait_4_3",       # 768x1024
        },
        "defaults": {
            # Same quality pinning as gpt-image-1.5: medium keeps Nous
            # Portal billing predictable. "high" is 3-4x the per-image
            # cost at the same size; "low" is too rough for production use.
            "quality": "medium",
            "num_images": 1,
            "output_format": "png",
        },
        "supports": {
            "prompt", "image_size", "quality", "num_images", "output_format",
            "sync_mode",
            # openai_api_key (BYOK) intentionally omitted — all users go
            # through the shared FAL billing path.
        },
        "upscale": False,
    },
    "fal-ai/ideogram/v3": {
        "display": "Ideogram V3",
        "speed": "~5s",
        "strengths": "Best typography",
        "price": "$0.03-0.09/image",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "rendering_speed": "BALANCED",
            "expand_prompt": True,
            "style": "AUTO",
        },
        "supports": {
            "prompt", "image_size", "rendering_speed", "expand_prompt",
            "style", "seed",
        },
        "upscale": False,
    },
    "fal-ai/recraft/v4/pro/text-to-image": {
        "display": "Recraft V4 Pro",
        "speed": "~8s",
        "strengths": "Design, brand systems, production-ready",
        "price": "$0.25/image",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            # V4 Pro dropped V3's required `style` enum — defaults handle taste now.
            "enable_safety_checker": False,
        },
        "supports": {
            "prompt", "image_size", "enable_safety_checker",
            "colors", "background_color",
        },
        "upscale": False,
    },
    "fal-ai/qwen-image": {
        "display": "Qwen Image",
        "speed": "~12s",
        "strengths": "LLM-based, complex text",
        "price": "$0.02/MP",
        "size_style": "image_size_preset",
        "sizes": {
            "landscape": "landscape_16_9",
            "square": "square_hd",
            "portrait": "portrait_16_9",
        },
        "defaults": {
            "num_inference_steps": 30,
            "guidance_scale": 2.5,
            "num_images": 1,
            "output_format": "png",
            "acceleration": "regular",
        },
        "supports": {
            "prompt", "image_size", "num_inference_steps", "guidance_scale",
            "num_images", "output_format", "acceleration", "seed", "sync_mode",
        },
        "upscale": False,
    },
}

# Default model is the fastest reasonable option. Kept cheap and sub-1s.
DEFAULT_MODEL = "fal-ai/flux-2/klein/9b"

DEFAULT_ASPECT_RATIO = "landscape"
VALID_ASPECT_RATIOS = ("landscape", "square", "portrait")


# ---------------------------------------------------------------------------
# Upscaler (Clarity Upscaler — unchanged from previous implementation)
# ---------------------------------------------------------------------------
UPSCALER_MODEL = "fal-ai/clarity-upscaler"
UPSCALER_FACTOR = 2
UPSCALER_SAFETY_CHECKER = False
UPSCALER_DEFAULT_PROMPT = "masterpiece, best quality, highres"
UPSCALER_NEGATIVE_PROMPT = "(worst quality, low quality, normal quality:2)"
UPSCALER_CREATIVITY = 0.35
UPSCALER_RESEMBLANCE = 0.6
UPSCALER_GUIDANCE_SCALE = 4
UPSCALER_NUM_INFERENCE_STEPS = 18


KIE_API_BASE_URL = os.getenv("KIE_AI_BASE_URL", "https://api.kie.ai").rstrip("/")
KIE_TASK_TIMEOUT_SECONDS = 180
KIE_POLL_INTERVAL_SECONDS = 3
KIE_SUPPORTED_MODELS = {
    "gpt-image-2-text-to-image": {
        "label": "gpt-image-2-text-to-image",
        "provider": "market",
        "model": "gpt-image-2-text-to-image",
    },
    "gpt image 2 text to image": {
        "label": "gpt-image-2-text-to-image",
        "provider": "market",
        "model": "gpt-image-2-text-to-image",
    },
    "4o image": {
        "label": "gpt-image-2-text-to-image",
        "provider": "market",
        "model": "gpt-image-2-text-to-image",
    },
    "4o": {
        "label": "gpt-image-2-text-to-image",
        "provider": "market",
        "model": "gpt-image-2-text-to-image",
    },
    "gpt-4o image": {
        "label": "gpt-image-2-text-to-image",
        "provider": "market",
        "model": "gpt-image-2-text-to-image",
    },
    "flux 2": {
        "label": "Flux 2",
        "provider": "market",
        "model": "flux-2/flex-text-to-image",
    },
    "flux2": {
        "label": "Flux 2",
        "provider": "market",
        "model": "flux-2/flex-text-to-image",
    },
    "imagen 4": {
        "label": "Imagen 4",
        "provider": "market",
        "model": "google/imagen4",
    },
    "imagen4": {
        "label": "Imagen 4",
        "provider": "market",
        "model": "google/imagen4",
    },
    "nano banana 2": {
        "label": "Nano Banana 2",
        "provider": "market",
        "model": "nano-banana-2",
    },
    "nano-banana-2": {
        "label": "Nano Banana 2",
        "provider": "market",
        "model": "nano-banana-2",
    },
    "nanobanana2": {
        "label": "Nano Banana 2",
        "provider": "market",
        "model": "nano-banana-2",
    },
}
KIE_MODEL_PROMPT = (
    "Для генерации изображения через kie.ai сначала уточни, какую модель использовать: "
    "gpt-image-2-text-to-image — универсально и лучше для текста в кадре; "
    "Flux 2 — если нужен более дизайнерский/продакшн-результат; "
    "Imagen 4 — если нужен фотореализм и рекламные креативы; "
    "Nano Banana 2 — если нужен быстрый и современный вариант."
)


GRSAI_API_BASE_URL = os.getenv("GRSAI_BASE_URL", "https://api.grsai.com/v1").rstrip("/")
GRSAI_TASK_TIMEOUT_SECONDS = 180
GRSAI_POLL_INTERVAL_SECONDS = 3
GRSAI_SUPPORTED_MODELS = {
    "gpt-image-2": {
        "label": "gpt-image-2",
        "endpoint": "/draw/completions",
        "request_format": "gpt_image",
        "api_model": "gpt-image-2",
    },
    "gpt image 2": {
        "label": "gpt-image-2",
        "endpoint": "/draw/completions",
        "request_format": "gpt_image",
        "api_model": "gpt-image-2",
    },
    "gpt-image-2-vip": {
        "label": "gpt-image-2-vip",
        "endpoint": "/draw/completions",
        "request_format": "gpt_image",
        "api_model": "gpt-image-2-vip",
    },
    "gpt image 2 vip": {
        "label": "gpt-image-2-vip",
        "endpoint": "/draw/completions",
        "request_format": "gpt_image",
        "api_model": "gpt-image-2-vip",
    },
    "imagen 4": {
        "label": "Imagen 4",
        "endpoint": "/draw/imagen",
        "request_format": "imagen",
    },
    "imagen4": {
        "label": "Imagen 4",
        "endpoint": "/draw/imagen",
        "request_format": "imagen",
    },
    "nano-banana-2": {
        "label": "nano-banana-2",
        "endpoint": "/draw/nano-banana",
        "request_format": "nano_banana",
        "image_size": "2K",
    },
    "nano banana 2": {
        "label": "nano-banana-2",
        "endpoint": "/draw/nano-banana",
        "request_format": "nano_banana",
        "image_size": "2K",
    },
    "nano-banana-fast": {
        "label": "nano-banana-fast",
        "endpoint": "/draw/nano-banana",
        "request_format": "nano_banana",
        "image_size": "1K",
    },
    "nano banana fast": {
        "label": "nano-banana-fast",
        "endpoint": "/draw/nano-banana",
        "request_format": "nano_banana",
        "image_size": "1K",
    },
    "nano-banana-pro": {
        "label": "nano-banana-pro",
        "endpoint": "/draw/nano-banana",
        "request_format": "nano_banana",
        "image_size": "2K",
    },
    "nano banana pro": {
        "label": "nano-banana-pro",
        "endpoint": "/draw/nano-banana",
        "request_format": "nano_banana",
        "image_size": "2K",
    },
}
GRSAI_MODEL_PROMPT = (
    "Для генерации изображения через GrsAI сначала уточни, какую модель использовать: "
    "gpt-image-2 — универсально и хорошо рисует текст; "
    "gpt-image-2-vip — более сильный вариант GPT Image; "
    "Imagen 4 — если нужен фотореализм; "
    "nano-banana-2 — если нужен современный креативный стиль; "
    "nano-banana-fast — если важна скорость; "
    "nano-banana-pro — если нужен максимум качества в Nano Banana."
)

_debug = DebugSession("image_tools", env_var="IMAGE_TOOLS_DEBUG")
_managed_fal_client = None
_managed_fal_client_config = None
_managed_fal_client_lock = threading.Lock()


def _get_image_provider(model_name: Optional[str] = None) -> str:
    normalized_model = " ".join(str(model_name or "").strip().lower().split())
    forced = str(os.getenv("IMAGE_GENERATION_PROVIDER", "")).strip().lower()
    if forced in {"grsai", "kie", "fal"}:
        return forced
    if os.getenv("GRSAI_API_KEY") and normalized_model in GRSAI_SUPPORTED_MODELS:
        return "grsai"
    if os.getenv("KIE_AI_API_KEY"):
        return "kie"
    if os.getenv("GRSAI_API_KEY"):
        return "grsai"
    return "fal"


def _resolve_kie_model(model_name: str) -> Dict[str, str]:
    normalized = " ".join(str(model_name or "").strip().lower().split())
    if not normalized:
        raise ValueError(KIE_MODEL_PROMPT)
    model_config = KIE_SUPPORTED_MODELS.get(normalized)
    if model_config is None:
        raise ValueError(
            f"Неизвестная модель '{model_name}'. Доступные варианты: gpt-image-2-text-to-image, Flux 2, Imagen 4, Nano Banana 2."
        )
    return model_config


def _kie_headers() -> Dict[str, str]:
    api_key = os.getenv("KIE_AI_API_KEY")
    if not api_key:
        raise ValueError("KIE_AI_API_KEY environment variable not set")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _grsai_headers() -> Dict[str, str]:
    api_key = os.getenv("GRSAI_API_KEY")
    if not api_key:
        raise ValueError("GRSAI_API_KEY environment variable not set")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_kie_image_url(task_payload: Dict[str, Any]) -> str:
    data = task_payload.get("data") if isinstance(task_payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("KIE API returned an invalid payload")

    response_payload = data.get("response")
    if isinstance(response_payload, dict):
        result_urls = response_payload.get("resultUrls") or response_payload.get("result_urls")
        if isinstance(result_urls, list) and result_urls:
            return result_urls[0]

    result_json = data.get("resultJson")
    if isinstance(result_json, str) and result_json.strip():
        parsed_result = json.loads(result_json)
    elif isinstance(result_json, dict):
        parsed_result = result_json
    else:
        parsed_result = {}

    result_urls = parsed_result.get("resultUrls")
    if isinstance(result_urls, list) and result_urls:
        return result_urls[0]

    images = data.get("images")
    if isinstance(images, list) and images:
        first_image = images[0]
        if isinstance(first_image, dict) and first_image.get("url"):
            return first_image["url"]
        if isinstance(first_image, str):
            return first_image

    raise ValueError("KIE API did not return any image URLs")


def _extract_grsai_task_id(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ValueError("GrsAI API returned an invalid payload")
    candidates = [
        payload.get("taskId"),
        payload.get("task_id"),
        payload.get("id"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([
            data.get("taskId"),
            data.get("task_id"),
            data.get("id"),
        ])
    for candidate in candidates:
        if candidate:
            return str(candidate)
    raise ValueError("GrsAI API did not return a taskId")


def _extract_grsai_image_url(task_payload: Dict[str, Any]) -> str:
    if not isinstance(task_payload, dict):
        raise ValueError("GrsAI API returned an invalid payload")

    def _first_url(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
            if isinstance(first, dict):
                for key in ("url", "imageUrl", "image_url"):
                    candidate = first.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        return None

    data = task_payload.get("data")
    candidates: list[Any] = [task_payload, data]
    for container in candidates:
        if not isinstance(container, dict):
            continue
        for key in (
            "resultUrls",
            "result_urls",
            "results",
            "imageUrls",
            "image_urls",
            "images",
            "output",
            "url",
            "imageUrl",
            "image_url",
        ):
            found = _first_url(container.get(key))
            if found:
                return found
        result_json = container.get("resultJson")
        if isinstance(result_json, str) and result_json.strip():
            try:
                parsed = json.loads(result_json)
            except Exception:
                parsed = None
        elif isinstance(result_json, dict):
            parsed = result_json
        else:
            parsed = None
        if isinstance(parsed, dict):
            found = _extract_grsai_image_url(parsed)
            if found:
                return found
    raise ValueError("GrsAI API did not return any image URLs")


def _normalize_kie_aspect_ratio(aspect_ratio: str) -> str:
    normalized = str(aspect_ratio or DEFAULT_ASPECT_RATIO).strip().lower()
    return {
        "landscape": "16:9",
        "square": "1:1",
        "portrait": "9:16",
    }.get(normalized, "1:1")


def _normalize_grsai_aspect_ratio(aspect_ratio: str) -> str:
    normalized = str(aspect_ratio or DEFAULT_ASPECT_RATIO).strip().lower()
    return {
        "landscape": "16:9",
        "square": "1:1",
        "portrait": "9:16",
    }.get(normalized, "1:1")


def _default_kie_resolution(model_config: Dict[str, str]) -> str:
    if model_config.get("model") == "nano-banana-2":
        return "2K"
    return "1K"


def _kie_local_image_dir() -> Path:
    hermes_home = Path(os.getenv("HERMES_HOME", "/opt/data"))
    target = hermes_home / "cache" / "images" / "generated"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _grsai_local_image_dir() -> Path:
    return _kie_local_image_dir()


def _download_kie_image_to_local(image_url: str, model_label: str) -> str:
    suffix = Path(image_url.split("?", 1)[0]).suffix.lower() or ".png"
    slug = (
        str(model_label or "kie")
        .strip()
        .lower()
        .replace(" ", "-")
        .replace("/", "-")
    )
    local_path = _kie_local_image_dir() / f"{slug}-{uuid.uuid4().hex}{suffix}"
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(image_url)
        response.raise_for_status()
        local_path.write_bytes(response.content)
    return str(local_path)


def _download_grsai_image_to_local(image_url: str, model_label: str) -> str:
    suffix = Path(image_url.split("?", 1)[0]).suffix.lower() or ".png"
    slug = (
        str(model_label or "grsai")
        .strip()
        .lower()
        .replace(" ", "-")
        .replace("/", "-")
    )
    local_path = _grsai_local_image_dir() / f"{slug}-{uuid.uuid4().hex}{suffix}"
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(image_url)
        response.raise_for_status()
        local_path.write_bytes(response.content)
    return str(local_path)


def _extract_grsai_sse_payload(raw_text: str) -> Dict[str, Any]:
    """Parse the terminal payload from a GrsAI SSE response."""
    final_payload: Optional[Dict[str, Any]] = None
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload_text = line[5:].strip()
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except Exception:
            continue
        if isinstance(payload, dict):
            final_payload = payload

    if not final_payload:
        raise ValueError("GrsAI SSE response did not contain a terminal payload")
    return final_payload


def _submit_kie_market_task(client: httpx.Client, model_config: Dict[str, str], prompt: str, aspect_ratio: str) -> str:
    response = client.post(
        f"{KIE_API_BASE_URL}/api/v1/jobs/createTask",
        headers=_kie_headers(),
        json={
            "model": model_config["model"],
            "input": {
                "prompt": prompt,
                "aspect_ratio": _normalize_kie_aspect_ratio(aspect_ratio),
                "resolution": _default_kie_resolution(model_config),
            },
        },
    )
    response.raise_for_status()
    payload = response.json()
    task_id = payload.get("data", {}).get("taskId") or payload.get("taskId")
    if not task_id:
        raise ValueError("KIE API did not return a taskId")
    return task_id


def _poll_kie_market_task(client: httpx.Client, task_id: str) -> Dict[str, Any]:
    deadline = time.time() + KIE_TASK_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = client.get(
            f"{KIE_API_BASE_URL}/api/v1/jobs/recordInfo",
            headers=_kie_headers(),
            params={"taskId": task_id},
        )
        response.raise_for_status()
        payload = response.json()
        task_data = payload.get("data", {}) if isinstance(payload, dict) else {}
        state = str(task_data.get("state", "")).lower()
        if state == "success":
            return payload
        if state == "fail":
            raise ValueError(task_data.get("failMsg") or "KIE image generation task failed")
        time.sleep(KIE_POLL_INTERVAL_SECONDS)

    raise TimeoutError("KIE image generation timed out")


def _generate_image_with_kie(prompt: str, aspect_ratio: str, model_name: Optional[str]) -> Dict[str, Any]:
    model_config = _resolve_kie_model(model_name)
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        task_id = _submit_kie_market_task(client, model_config, prompt, aspect_ratio)
        payload = _poll_kie_market_task(client, task_id)

    image_url = _extract_kie_image_url(payload)
    local_path = None
    try:
        local_path = _download_kie_image_to_local(image_url, model_config["label"])
    except Exception as exc:
        logger.warning("Failed to download KIE image locally: %s", exc)
    return {
        "success": True,
        "image": image_url,
        "local_path": local_path,
        "provider": "kie",
        "model": model_config["label"],
    }


def _resolve_grsai_model(model_name: str) -> Dict[str, str]:
    normalized = " ".join(str(model_name or "").strip().lower().split())
    if not normalized:
        raise ValueError(GRSAI_MODEL_PROMPT)
    model_config = GRSAI_SUPPORTED_MODELS.get(normalized)
    if model_config is None:
        raise ValueError(
            "Неизвестная модель GrsAI "
            f"'{model_name}'. Доступные варианты: gpt-image-2, gpt-image-2-vip, Imagen 4, "
            "nano-banana-2, nano-banana-fast, nano-banana-pro."
        )
    return model_config


def _build_grsai_image_payload(model_config: Dict[str, str], prompt: str, aspect_ratio: str) -> Dict[str, Any]:
    normalized_ratio = _normalize_grsai_aspect_ratio(aspect_ratio)
    request_format = model_config.get("request_format", "gpt_image")
    if request_format == "gpt_image":
        payload = {
            "model": model_config.get("api_model", model_config["label"]),
            "prompt": prompt,
            "size": "auto",
            "aspect_ratio": normalized_ratio,
            "n": 1,
        }
        return payload
    if request_format == "imagen":
        return {
            "prompt": prompt,
            "aspect_ratio": normalized_ratio,
        }
    if request_format == "nano_banana":
        return {
            "prompt": prompt,
            "aspect_ratio": normalized_ratio,
            "image_size": model_config.get("image_size", "1K"),
        }
    raise ValueError(f"Unsupported GrsAI request format: {request_format}")


def _submit_grsai_task(
    client: httpx.Client,
    model_config: Dict[str, str],
    prompt: str,
    aspect_ratio: str,
) -> Union[str, Dict[str, Any]]:
    endpoint = model_config["endpoint"]
    response = client.post(
        f"{GRSAI_API_BASE_URL}{endpoint}",
        headers=_grsai_headers(),
        json=_build_grsai_image_payload(model_config, prompt, aspect_ratio),
    )
    response.raise_for_status()
    content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
    if "text/event-stream" in content_type:
        return _extract_grsai_sse_payload(response.text)
    payload = response.json()
    return _extract_grsai_task_id(payload)


def _poll_grsai_task(client: httpx.Client, task_id: str) -> Dict[str, Any]:
    deadline = time.time() + GRSAI_TASK_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = client.post(
            f"{GRSAI_API_BASE_URL}/draw/result",
            headers=_grsai_headers(),
            json={"taskId": task_id},
        )
        response.raise_for_status()
        payload = response.json()
        task_data = payload.get("data", {}) if isinstance(payload, dict) else {}
        state = str(
            task_data.get("state")
            or task_data.get("status")
            or payload.get("state")
            or payload.get("status")
            or ""
        ).lower()
        if state in {"success", "succeeded", "done", "completed"}:
            return payload
        if state in {"fail", "failed", "error"}:
            raise ValueError(
                task_data.get("failMsg")
                or task_data.get("message")
                or payload.get("message")
                or "GrsAI image generation task failed"
            )
        time.sleep(GRSAI_POLL_INTERVAL_SECONDS)

    raise TimeoutError("GrsAI image generation timed out")


def _generate_image_with_grsai(prompt: str, aspect_ratio: str, model_name: Optional[str]) -> Dict[str, Any]:
    model_config = _resolve_grsai_model(model_name or "")
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        submit_result = _submit_grsai_task(client, model_config, prompt, aspect_ratio)
        if isinstance(submit_result, dict):
            payload = submit_result
        else:
            payload = _poll_grsai_task(client, submit_result)

    image_url = _extract_grsai_image_url(payload)
    local_path = None
    try:
        local_path = _download_grsai_image_to_local(image_url, model_config["label"])
    except Exception as exc:
        logger.warning("Failed to download GrsAI image locally: %s", exc)
    return {
        "success": True,
        "image": image_url,
        "local_path": local_path,
        "provider": "grsai",
        "model": model_config["label"],
    }

# ---------------------------------------------------------------------------
# Managed FAL gateway (Nous Subscription)
# ---------------------------------------------------------------------------
def _resolve_managed_fal_gateway():
    """Return managed fal-queue gateway config when the user prefers the gateway
    or direct FAL credentials are absent."""
    if fal_key_is_configured() and not prefers_gateway("image_gen"):
        return None
    return resolve_managed_tool_gateway("fal-queue")


def _normalize_fal_queue_url_format(queue_run_origin: str) -> str:
    normalized_origin = str(queue_run_origin or "").strip().rstrip("/")
    if not normalized_origin:
        raise ValueError("Managed FAL queue origin is required")
    return f"{normalized_origin}/"


class _ManagedFalSyncClient:
    """Small per-instance wrapper around fal_client.SyncClient for managed queue hosts."""

    def __init__(self, *, key: str, queue_run_origin: str):
        if fal_client is None:
            raise RuntimeError("fal_client is required for managed FAL gateway mode")
        sync_client_class = getattr(fal_client, "SyncClient", None)
        if sync_client_class is None:
            raise RuntimeError("fal_client.SyncClient is required for managed FAL gateway mode")

        client_module = getattr(fal_client, "client", None)
        if client_module is None:
            raise RuntimeError("fal_client.client is required for managed FAL gateway mode")

        self._queue_url_format = _normalize_fal_queue_url_format(queue_run_origin)
        self._sync_client = sync_client_class(key=key)
        self._http_client = getattr(self._sync_client, "_client", None)
        self._maybe_retry_request = getattr(client_module, "_maybe_retry_request", None)
        self._raise_for_status = getattr(client_module, "_raise_for_status", None)
        self._request_handle_class = getattr(client_module, "SyncRequestHandle", None)
        self._add_hint_header = getattr(client_module, "add_hint_header", None)
        self._add_priority_header = getattr(client_module, "add_priority_header", None)
        self._add_timeout_header = getattr(client_module, "add_timeout_header", None)

        if self._http_client is None:
            raise RuntimeError("fal_client.SyncClient._client is required for managed FAL gateway mode")
        if self._maybe_retry_request is None or self._raise_for_status is None:
            raise RuntimeError("fal_client.client request helpers are required for managed FAL gateway mode")
        if self._request_handle_class is None:
            raise RuntimeError("fal_client.client.SyncRequestHandle is required for managed FAL gateway mode")

    def submit(
        self,
        application: str,
        arguments: Dict[str, Any],
        *,
        path: str = "",
        hint: Optional[str] = None,
        webhook_url: Optional[str] = None,
        priority: Any = None,
        headers: Optional[Dict[str, str]] = None,
        start_timeout: Optional[Union[int, float]] = None,
    ):
        url = self._queue_url_format + application
        if path:
            url += "/" + path.lstrip("/")
        if webhook_url is not None:
            url += "?" + urlencode({"fal_webhook": webhook_url})

        request_headers = dict(headers or {})
        if hint is not None and self._add_hint_header is not None:
            self._add_hint_header(hint, request_headers)
        if priority is not None:
            if self._add_priority_header is None:
                raise RuntimeError("fal_client.client.add_priority_header is required for priority requests")
            self._add_priority_header(priority, request_headers)
        if start_timeout is not None:
            if self._add_timeout_header is None:
                raise RuntimeError("fal_client.client.add_timeout_header is required for timeout requests")
            self._add_timeout_header(start_timeout, request_headers)

        response = self._maybe_retry_request(
            self._http_client,
            "POST",
            url,
            json=arguments,
            timeout=getattr(self._sync_client, "default_timeout", 120.0),
            headers=request_headers,
        )
        self._raise_for_status(response)

        data = response.json()
        return self._request_handle_class(
            request_id=data["request_id"],
            response_url=data["response_url"],
            status_url=data["status_url"],
            cancel_url=data["cancel_url"],
            client=self._http_client,
        )


def _get_managed_fal_client(managed_gateway):
    """Reuse the managed FAL client so its internal httpx.Client is not leaked per call."""
    global _managed_fal_client, _managed_fal_client_config

    client_config = (
        managed_gateway.gateway_origin.rstrip("/"),
        managed_gateway.nous_user_token,
    )
    with _managed_fal_client_lock:
        if _managed_fal_client is not None and _managed_fal_client_config == client_config:
            return _managed_fal_client

        _managed_fal_client = _ManagedFalSyncClient(
            key=managed_gateway.nous_user_token,
            queue_run_origin=managed_gateway.gateway_origin,
        )
        _managed_fal_client_config = client_config
        return _managed_fal_client


def _submit_fal_request(model: str, arguments: Dict[str, Any]):
    """Submit a FAL request using direct credentials or the managed queue gateway."""
    if fal_client is None:
        raise RuntimeError("fal_client is not installed")
    request_headers = {"x-idempotency-key": str(uuid.uuid4())}
    managed_gateway = _resolve_managed_fal_gateway()
    if managed_gateway is None:
        return fal_client.submit(model, arguments=arguments, headers=request_headers)

    managed_client = _get_managed_fal_client(managed_gateway)
    try:
        return managed_client.submit(
            model,
            arguments=arguments,
            headers=request_headers,
        )
    except Exception as exc:
        # 4xx from the managed gateway typically means the portal doesn't
        # currently proxy this model (allowlist miss, billing gate, etc.)
        # — surface a clearer message with actionable remediation instead
        # of a raw HTTP error from httpx.
        status = _extract_http_status(exc)
        if status is not None and 400 <= status < 500:
            raise ValueError(
                f"Nous Subscription gateway rejected model '{model}' "
                f"(HTTP {status}). This model may not yet be enabled on "
                f"the Nous Portal's FAL proxy. Either:\n"
                f"  • Set FAL_KEY in your environment to use FAL.ai directly, or\n"
                f"  • Pick a different model via `hermes tools` → Image Generation."
            ) from exc
        raise


def _extract_http_status(exc: BaseException) -> Optional[int]:
    """Return an HTTP status code from httpx/fal exceptions, else None.

    Defensive across exception shapes — httpx.HTTPStatusError exposes
    ``.response.status_code`` while fal_client wrappers may expose
    ``.status_code`` directly.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    return None


# ---------------------------------------------------------------------------
# Model resolution + payload construction
# ---------------------------------------------------------------------------
def _resolve_fal_model() -> tuple:
    """Resolve the active FAL model from config.yaml (primary) or default.

    Returns (model_id, metadata_dict). Falls back to DEFAULT_MODEL if the
    configured model is unknown (logged as a warning).
    """
    model_id = ""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        img_cfg = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(img_cfg, dict):
            raw = img_cfg.get("model")
            if isinstance(raw, str):
                model_id = raw.strip()
    except Exception as exc:
        logger.debug("Could not load image_gen.model from config: %s", exc)

    # Env var escape hatch (undocumented; backward-compat for tests/scripts).
    if not model_id:
        model_id = os.getenv("FAL_IMAGE_MODEL", "").strip()

    if not model_id:
        return DEFAULT_MODEL, FAL_MODELS[DEFAULT_MODEL]

    if model_id not in FAL_MODELS:
        logger.warning(
            "Unknown FAL model '%s' in config; falling back to %s",
            model_id, DEFAULT_MODEL,
        )
        return DEFAULT_MODEL, FAL_MODELS[DEFAULT_MODEL]

    return model_id, FAL_MODELS[model_id]


def _build_fal_payload(
    model_id: str,
    prompt: str,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    seed: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a FAL request payload for `model_id` from unified inputs.

    Translates aspect_ratio into the model's native size spec (preset enum,
    aspect-ratio enum, or GPT literal string), merges model defaults, applies
    caller overrides, then filters to the model's ``supports`` whitelist.
    """
    meta = FAL_MODELS[model_id]
    size_style = meta["size_style"]
    sizes = meta["sizes"]

    aspect = (aspect_ratio or DEFAULT_ASPECT_RATIO).lower().strip()
    if aspect not in sizes:
        aspect = DEFAULT_ASPECT_RATIO

    payload: Dict[str, Any] = dict(meta.get("defaults", {}))
    payload["prompt"] = (prompt or "").strip()

    if size_style in ("image_size_preset", "gpt_literal"):
        payload["image_size"] = sizes[aspect]
    elif size_style == "aspect_ratio":
        payload["aspect_ratio"] = sizes[aspect]
    else:
        raise ValueError(f"Unknown size_style: {size_style!r}")

    if seed is not None and isinstance(seed, int):
        payload["seed"] = seed

    if overrides:
        for k, v in overrides.items():
            if v is not None:
                payload[k] = v

    supports = meta["supports"]
    return {k: v for k, v in payload.items() if k in supports}


# ---------------------------------------------------------------------------
# Upscaler
# ---------------------------------------------------------------------------
def _upscale_image(image_url: str, original_prompt: str) -> Optional[Dict[str, Any]]:
    """Upscale an image using FAL.ai's Clarity Upscaler.

    Returns upscaled image dict, or None on failure (caller falls back to
    the original image).
    """
    try:
        logger.info("Upscaling image with Clarity Upscaler...")

        upscaler_arguments = {
            "image_url": image_url,
            "prompt": f"{UPSCALER_DEFAULT_PROMPT}, {original_prompt}",
            "upscale_factor": UPSCALER_FACTOR,
            "negative_prompt": UPSCALER_NEGATIVE_PROMPT,
            "creativity": UPSCALER_CREATIVITY,
            "resemblance": UPSCALER_RESEMBLANCE,
            "guidance_scale": UPSCALER_GUIDANCE_SCALE,
            "num_inference_steps": UPSCALER_NUM_INFERENCE_STEPS,
            "enable_safety_checker": UPSCALER_SAFETY_CHECKER,
        }

        handler = _submit_fal_request(UPSCALER_MODEL, arguments=upscaler_arguments)
        result = handler.get()

        if result and "image" in result:
            upscaled_image = result["image"]
            logger.info(
                "Image upscaled successfully to %sx%s",
                upscaled_image.get("width", "unknown"),
                upscaled_image.get("height", "unknown"),
            )
            return {
                "url": upscaled_image["url"],
                "width": upscaled_image.get("width", 0),
                "height": upscaled_image.get("height", 0),
                "upscaled": True,
                "upscale_factor": UPSCALER_FACTOR,
            }
        logger.error("Upscaler returned invalid response")
        return None

    except Exception as e:
        logger.error("Error upscaling image: %s", e, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------
def image_generate_tool(
    prompt: str,
    model: Optional[str] = None,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    num_images: Optional[int] = None,
    output_format: Optional[str] = None,
    seed: Optional[int] = None,
) -> str:
    """Generate an image from a text prompt using the configured FAL model.

    The agent-facing schema exposes only ``prompt`` and ``aspect_ratio``; the
    remaining kwargs are overrides for direct Python callers and are filtered
    per-model via the ``supports`` whitelist (unsupported overrides are
    silently dropped so legacy callers don't break when switching models).

    Returns a JSON string with ``{"success": bool, "image": url | None,
    "error": str, "error_type": str}``.
    """
    provider = _get_image_provider(model)
    model_id = model or ""
    meta: Dict[str, Any] = {}

    debug_call_data = {
        "model": model or model_id,
        "parameters": {
            "prompt": prompt,
            "model": model,
            "aspect_ratio": aspect_ratio,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "num_images": num_images,
            "output_format": output_format,
            "seed": seed,
        },
        "error": None,
        "success": False,
        "images_generated": 0,
        "generation_time": 0,
    }

    start_time = datetime.datetime.now()

    try:
        logger.info(
            "Generating %s image(s) with %s provider: %s",
            num_images, provider, prompt[:80],
        )

        if not prompt or not isinstance(prompt, str) or len(prompt.strip()) == 0:
            raise ValueError("Prompt is required and must be a non-empty string")

        aspect_lc = (aspect_ratio or DEFAULT_ASPECT_RATIO).lower().strip()
        if aspect_lc not in VALID_ASPECT_RATIOS:
            logger.warning(
                "Invalid aspect_ratio '%s', defaulting to '%s'",
                aspect_ratio, DEFAULT_ASPECT_RATIO,
            )
            aspect_lc = DEFAULT_ASPECT_RATIO

        if provider == "kie":
            if not model:
                raise ValueError(KIE_MODEL_PROMPT)
            result = _generate_image_with_kie(prompt.strip(), aspect_lc, model)
            generation_time = (datetime.datetime.now() - start_time).total_seconds()
            debug_call_data["success"] = True
            debug_call_data["images_generated"] = 1
            debug_call_data["generation_time"] = generation_time
            _debug.log_call("image_generate_tool", debug_call_data)
            _debug.save()
            return json.dumps(result, indent=2, ensure_ascii=False)

        if provider == "grsai":
            if not model:
                raise ValueError(GRSAI_MODEL_PROMPT)
            result = _generate_image_with_grsai(prompt.strip(), aspect_lc, model)
            generation_time = (datetime.datetime.now() - start_time).total_seconds()
            debug_call_data["success"] = True
            debug_call_data["images_generated"] = 1
            debug_call_data["generation_time"] = generation_time
            _debug.log_call("image_generate_tool", debug_call_data)
            _debug.save()
            return json.dumps(result, indent=2, ensure_ascii=False)

        if not (fal_key_is_configured() or _resolve_managed_fal_gateway()):
            message = "FAL_KEY environment variable not set"
            if managed_nous_tools_enabled():
                message += " and managed FAL gateway is unavailable"
            raise ValueError(message)

        model_id, meta = _resolve_fal_model()
        debug_call_data["model"] = model or model_id

        overrides: Dict[str, Any] = {}
        if num_inference_steps is not None:
            overrides["num_inference_steps"] = num_inference_steps
        if guidance_scale is not None:
            overrides["guidance_scale"] = guidance_scale
        if num_images is not None:
            overrides["num_images"] = num_images
        if output_format is not None:
            overrides["output_format"] = output_format

        arguments = _build_fal_payload(
            model_id, prompt, aspect_lc, seed=seed, overrides=overrides,
        )

        logger.info(
            "Generating image with %s (%s) — prompt: %s",
            meta.get("display", model_id), model_id, prompt[:80],
        )

        handler = _submit_fal_request(model_id, arguments=arguments)
        result = handler.get()

        generation_time = (datetime.datetime.now() - start_time).total_seconds()

        if not result or "images" not in result:
            raise ValueError("Invalid response from FAL.ai API — no images returned")

        images = result.get("images", [])
        if not images:
            raise ValueError("No images were generated")

        should_upscale = bool(meta.get("upscale", False))

        formatted_images = []
        for img in images:
            if not (isinstance(img, dict) and "url" in img):
                continue
            original_image = {
                "url": img["url"],
                "width": img.get("width", 0),
                "height": img.get("height", 0),
            }

            if should_upscale:
                upscaled_image = _upscale_image(img["url"], prompt.strip())
                if upscaled_image:
                    formatted_images.append(upscaled_image)
                    continue
                logger.warning("Using original image as fallback (upscale failed)")

            original_image["upscaled"] = False
            formatted_images.append(original_image)

        if not formatted_images:
            raise ValueError("No valid image URLs returned from API")

        upscaled_count = sum(1 for img in formatted_images if img.get("upscaled"))
        logger.info(
            "Generated %s image(s) in %.1fs (%s upscaled) via %s",
            len(formatted_images), generation_time, upscaled_count, model_id,
        )

        response_data = {
            "success": True,
            "image": formatted_images[0]["url"] if formatted_images else None,
        }

        debug_call_data["success"] = True
        debug_call_data["images_generated"] = len(formatted_images)
        debug_call_data["generation_time"] = generation_time
        _debug.log_call("image_generate_tool", debug_call_data)
        _debug.save()

        return json.dumps(response_data, indent=2, ensure_ascii=False)

    except Exception as e:
        generation_time = (datetime.datetime.now() - start_time).total_seconds()
        error_msg = f"Error generating image: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)

        response_data = {
            "success": False,
            "image": None,
            "error": str(e),
            "error_type": type(e).__name__,
        }

        debug_call_data["error"] = error_msg
        debug_call_data["generation_time"] = generation_time
        _debug.log_call("image_generate_tool", debug_call_data)
        _debug.save()

        return json.dumps(response_data, indent=2, ensure_ascii=False)


def check_fal_api_key() -> bool:
    """True if any built-in image backend credentials are available."""
    return bool(
        fal_key_is_configured()
        or _resolve_managed_fal_gateway()
        or os.getenv("KIE_AI_API_KEY")
        or os.getenv("GRSAI_API_KEY")
    )


def check_image_generation_requirements() -> bool:
    """True if any image gen backend is available.

    Providers are considered in this order:

    1. The in-tree FAL backend (FAL_KEY or managed gateway).
    2. Any plugin-registered provider whose ``is_available()`` returns True.

    Plugins win only when the in-tree FAL path is NOT ready, which matches
    the historical behavior: shipping hermes with a FAL key configured
    should still expose the tool. The active selection among ready
    providers is resolved per-call by ``image_gen.provider``.
    """
    try:
        if check_fal_api_key():
            if os.getenv("KIE_AI_API_KEY") or os.getenv("GRSAI_API_KEY"):
                httpx  # noqa: F401 — dependency presence check
                return True
            if fal_client is None:
                raise ImportError("fal_client library not available")
            return True
    except ImportError:
        pass

    # Probe plugin providers. Discovery is idempotent and cheap.
    try:
        from agent.image_gen_registry import list_providers
        from hermes_cli.plugins import _ensure_plugins_discovered

        _ensure_plugins_discovered()
        for provider in list_providers():
            try:
                if provider.is_available():
                    return True
            except Exception:
                continue
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Demo / CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("🎨 Image Generation Tools — multi-provider support")
    print("=" * 60)

    if not check_fal_api_key():
        print("❌ Ни FAL_KEY, ни KIE_AI_API_KEY, ни GRSAI_API_KEY не настроены")
        print("   Для FAL: export FAL_KEY='your-key-here'")
        print("   Для GrsAI: export GRSAI_API_KEY='your-key-here'")
        print("   Для KIE: export KIE_AI_API_KEY='your-key-here'")
        raise SystemExit(1)
    print("✅ Image generation credentials found")

    if _get_image_provider() == "fal":
        if fal_client is None:
            print("❌ fal_client library not found — pip install fal-client")
            raise SystemExit(1)
        print("✅ fal_client library available")

    model_id, meta = _resolve_fal_model()
    print(f"🤖 Active model: {meta.get('display', model_id)} ({model_id})")
    print(f"   Speed: {meta.get('speed', '?')}  ·  Price: {meta.get('price', '?')}")
    print(f"   Upscaler: {'on' if meta.get('upscale') else 'off'}")

    print("\nAvailable models:")
    for mid, m in FAL_MODELS.items():
        marker = " ← active" if mid == model_id else ""
        print(f"  {mid:<32}  {m.get('speed', '?'):<6}  {m.get('price', '?')}{marker}")
    if _debug.active:
        print(f"\n🐛 Debug mode enabled — session {_debug.session_id}")


def get_debug_session_info() -> Dict[str, Any]:
    """Return information about the current debug session."""
    return _debug.get_session_info()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

IMAGE_GENERATE_SCHEMA = {
    "name": "image_generate",
    "description": (
        "Generate high-quality images from text prompts. The underlying "
        "backend is user-configured and may use FAL, KIE, GrsAI, or a "
        "plugin provider. If the active backend requires an explicit model "
        "and the user did not specify one, ask first. Returns either a URL "
        "or an absolute file path in the `image` field; display it with "
        "markdown ![description](url-or-path) and the gateway will deliver it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The text prompt describing the desired image. Be detailed and descriptive.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(VALID_ASPECT_RATIOS),
                "description": "The aspect ratio of the generated image. 'landscape' is 16:9 wide, 'portrait' is 16:9 tall, 'square' is 1:1.",
                "default": DEFAULT_ASPECT_RATIO,
            },
        },
        "required": ["prompt"],
    },
}


def _read_configured_image_model():
    """Return the value of ``image_gen.model`` from config.yaml, or None."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(section, dict):
            value = section.get("model")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as exc:
        logger.debug("Could not read image_gen.model: %s", exc)
    return None


def _read_configured_image_provider():
    """Return the value of ``image_gen.provider`` from config.yaml, or None.

    We only consult the plugin registry when this is explicitly set — an
    unset value keeps users on the legacy in-tree FAL path even when other
    providers happen to be registered (e.g. a user has OPENAI_API_KEY set
    for other features but never asked for OpenAI image gen).
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(section, dict):
            value = section.get("provider")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as exc:
        logger.debug("Could not read image_gen.provider: %s", exc)
    return None


def _dispatch_to_plugin_provider(prompt: str, aspect_ratio: str):
    """Route the call to a plugin-registered provider when one is selected.

    Returns a JSON string on dispatch, or ``None`` to fall through to the
    built-in FAL path.

    Dispatch only fires when ``image_gen.provider`` is explicitly set AND
    it does not point to one of the built-in backends. Any other value that
    matches a registered plugin provider wins.
    """
    configured = _read_configured_image_provider()
    if not configured or configured in {"fal", "kie", "grsai"}:
        return None

    # Also read configured model so we can pass it to the plugin
    configured_model = _read_configured_image_model()

    try:
        # Import locally so plugin discovery isn't triggered just by
        # importing this module (tests rely on that).
        from agent.image_gen_registry import get_provider
        from hermes_cli.plugins import _ensure_plugins_discovered

        _ensure_plugins_discovered()
        provider = get_provider(configured)
    except Exception as exc:
        logger.debug("image_gen plugin dispatch skipped: %s", exc)
        return None

    if provider is None:
        try:
            # Long-lived sessions may have discovered plugins before a bundled
            # backend was patched in or before config changed. Retry once with
            # a forced refresh before surfacing a missing-provider error.
            _ensure_plugins_discovered(force=True)
            provider = get_provider(configured)
        except Exception as exc:
            logger.debug("image_gen plugin force-refresh skipped: %s", exc)

    if provider is None:
        return json.dumps({
            "success": False,
            "image": None,
            "error": (
                f"image_gen.provider='{configured}' is set but no plugin "
                f"registered that name. Run `hermes plugins list` to see "
                f"available image gen backends."
            ),
            "error_type": "provider_not_registered",
        })

    try:
        kwargs = {"prompt": prompt, "aspect_ratio": aspect_ratio}
        if configured_model:
            kwargs["model"] = configured_model
        result = provider.generate(**kwargs)
    except Exception as exc:
        logger.warning(
            "Image gen provider '%s' raised: %s",
            getattr(provider, "name", "?"), exc,
        )
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"Provider '{getattr(provider, 'name', '?')}' error: {exc}",
            "error_type": "provider_exception",
        })
    if not isinstance(result, dict):
        return json.dumps({
            "success": False,
            "image": None,
            "error": "Provider returned a non-dict result",
            "error_type": "provider_contract",
        })
    return json.dumps(result)


def _handle_image_generate(args, **kw):
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for image generation")
    aspect_ratio = args.get("aspect_ratio", DEFAULT_ASPECT_RATIO)
    model = args.get("model") or _read_configured_image_model()

    # Route to a plugin-registered provider if one is active (and it's
    # not the in-tree FAL path).
    dispatched = _dispatch_to_plugin_provider(prompt, aspect_ratio)
    if dispatched is not None:
        return dispatched

    provider = _get_image_provider(model)
    if provider == "kie" and not model:
        return tool_error(KIE_MODEL_PROMPT, success=False)
    if provider == "grsai" and not model:
        return tool_error(GRSAI_MODEL_PROMPT, success=False)
    return image_generate_tool(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        num_inference_steps=50,
        guidance_scale=4.5,
        num_images=1,
        output_format="png",
    )


registry.register(
    name="image_generate",
    toolset="image_gen",
    schema=IMAGE_GENERATE_SCHEMA,
    handler=_handle_image_generate,
    check_fn=check_image_generation_requirements,
    requires_env=[],
    is_async=False,   # sync fal_client API to avoid "Event loop is closed" in gateway
    emoji="🎨",
)
