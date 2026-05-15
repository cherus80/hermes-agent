import json
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.error import URLError

import pytest


TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"


def _load_tool_module(module_name: str, filename: str):
    spec = spec_from_file_location(module_name, TOOLS_DIR / filename)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _restore_tool_modules():
    original_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "tools"
        or name.startswith("tools.")
        or name in {"fal_client"}
    }
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools.") or name == "fal_client":
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)


def _install_fake_tools_package():
    tools_package = types.ModuleType("tools")
    tools_package.__path__ = [str(TOOLS_DIR)]  # type: ignore[attr-defined]
    sys.modules["tools"] = tools_package
    sys.modules["tools.debug_helpers"] = types.SimpleNamespace(
        DebugSession=lambda *args, **kwargs: types.SimpleNamespace(
            active=False,
            session_id="debug-session",
            log_call=lambda *a, **k: None,
            save=lambda: None,
            get_session_info=lambda: {},
        )
    )
    sys.modules["tools.managed_tool_gateway"] = types.SimpleNamespace(
        resolve_managed_tool_gateway=lambda *args, **kwargs: None
    )
    sys.modules["tools.tool_backend_helpers"] = types.SimpleNamespace(
        managed_nous_tools_enabled=lambda: False
    )
    _load_tool_module("tools.registry", "registry.py")


def _install_fake_fal_client():
    sys.modules["fal_client"] = types.SimpleNamespace(
        submit=lambda *args, **kwargs: None,
        SyncClient=object,
        client=types.SimpleNamespace(),
    )


def test_handle_image_generate_requires_model_for_kie(monkeypatch):
    _install_fake_tools_package()
    _install_fake_fal_client()
    monkeypatch.setenv("KIE_AI_API_KEY", "kie-test-key")

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    result = json.loads(
        image_generation_tool._handle_image_generate({"prompt": "Нарисуй обложку для поста"})
    )

    assert result["success"] is False
    assert "какую модель" in result["error"].lower()
    assert "gpt-image-2-text-to-image" in result["error"]
    assert "Flux 2" in result["error"]
    assert "Imagen 4" in result["error"]
    assert "Nano Banana 2" in result["error"]


def test_extracts_first_result_url_from_kie_task_payload(monkeypatch):
    _install_fake_tools_package()
    _install_fake_fal_client()

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    task_payload = {
        "data": {
            "state": "success",
            "resultJson": json.dumps(
                {
                    "resultUrls": [
                        "https://cdn.kie.ai/generated-1.png",
                        "https://cdn.kie.ai/generated-2.png",
                    ]
                }
            ),
        }
    }

    assert image_generation_tool._extract_kie_image_url(task_payload) == "https://cdn.kie.ai/generated-1.png"


def test_resolves_kie_model_aliases():
    _install_fake_tools_package()
    _install_fake_fal_client()

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    assert image_generation_tool._resolve_kie_model("gpt-image-2-text-to-image")["model"] == "gpt-image-2-text-to-image"
    assert image_generation_tool._resolve_kie_model("4o Image")["model"] == "gpt-image-2-text-to-image"
    assert image_generation_tool._resolve_kie_model("Flux 2")["provider"] == "market"
    assert image_generation_tool._resolve_kie_model("Imagen 4")["model"] == "google/imagen4"
    assert image_generation_tool._resolve_kie_model("Nano Banana 2")["model"] == "nano-banana-2"
    assert image_generation_tool._resolve_kie_model("flux2")["label"] == "Flux 2"


def test_submit_kie_market_task_uses_gpt_image_2_payload(monkeypatch):
    _install_fake_tools_package()
    _install_fake_fal_client()
    monkeypatch.setenv("KIE_AI_API_KEY", "kie-test-key")

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"taskId": "task-gpt-image-2"}}

    class _FakeClient:
        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    task_id = image_generation_tool._submit_kie_market_task(
        _FakeClient(),
        image_generation_tool._resolve_kie_model("gpt-image-2-text-to-image"),
        "Poster with bold readable Cyrillic text",
        "portrait",
    )

    assert task_id == "task-gpt-image-2"
    assert captured["url"].endswith("/api/v1/jobs/createTask")
    assert captured["json"] == {
        "model": "gpt-image-2-text-to-image",
        "input": {
            "prompt": "Poster with bold readable Cyrillic text",
            "aspect_ratio": "9:16",
            "resolution": "1K",
        },
    }


def test_downloads_kie_image_to_local_cache(monkeypatch, tmp_path):
    _install_fake_tools_package()
    _install_fake_fal_client()

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    monkeypatch.setattr(
        image_generation_tool,
        "_kie_local_image_dir",
        lambda: tmp_path,
    )

    downloaded = {}

    def _fake_urlretrieve(url, destination):
        Path(destination).write_bytes(b"png-bytes")
        downloaded["url"] = url
        downloaded["destination"] = destination
        return destination, None

    monkeypatch.setattr(image_generation_tool.urllib.request, "urlretrieve", _fake_urlretrieve)

    result = image_generation_tool._download_kie_image_to_local(
        "https://tempfile.aiquickdraw.com/h/example.png",
        "Flux 2",
    )

    assert result.startswith(str(tmp_path))
    assert Path(result).read_bytes() == b"png-bytes"
    assert downloaded["url"] == "https://tempfile.aiquickdraw.com/h/example.png"


def test_generate_image_with_kie_returns_local_path(monkeypatch, tmp_path):
    _install_fake_tools_package()
    _install_fake_fal_client()

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(image_generation_tool.httpx, "Client", _FakeClient)
    monkeypatch.setattr(image_generation_tool, "_submit_kie_market_task", lambda *args, **kwargs: "task-123")
    monkeypatch.setattr(
        image_generation_tool,
        "_poll_kie_market_task",
        lambda *args, **kwargs: {"data": {"state": "success", "resultJson": {"resultUrls": ["https://tempfile.aiquickdraw.com/h/test.png"]}}},
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_download_kie_image_to_local",
        lambda url, label: str(tmp_path / "generated.png"),
    )

    result = image_generation_tool._generate_image_with_kie("prompt", "portrait", "Flux 2")

    assert result["success"] is True
    assert result["image"] == "https://tempfile.aiquickdraw.com/h/test.png"
    assert result["local_path"] == str(tmp_path / "generated.png")
    assert result["provider"] == "kie"
    assert result["model"] == "Flux 2"


def test_generate_image_with_kie_tolerates_local_download_failure(monkeypatch):
    _install_fake_tools_package()
    _install_fake_fal_client()

    image_generation_tool = _load_tool_module(
        "tools.image_generation_tool",
        "image_generation_tool.py",
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(image_generation_tool.httpx, "Client", _FakeClient)
    monkeypatch.setattr(image_generation_tool, "_submit_kie_market_task", lambda *args, **kwargs: "task-123")
    monkeypatch.setattr(
        image_generation_tool,
        "_poll_kie_market_task",
        lambda *args, **kwargs: {"data": {"state": "success", "resultJson": {"resultUrls": ["https://tempfile.aiquickdraw.com/h/test.png"]}}},
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_download_kie_image_to_local",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("download failed")),
    )

    result = image_generation_tool._generate_image_with_kie("prompt", "portrait", "Flux 2")

    assert result["success"] is True
    assert result["image"] == "https://tempfile.aiquickdraw.com/h/test.png"
    assert result["local_path"] is None
