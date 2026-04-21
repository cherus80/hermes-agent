import json
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"


def _load_tool_module(module_name: str, filename: str):
    spec = spec_from_file_location(module_name, TOOLS_DIR / filename)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _install_fake_tools_package():
    tools_package = types.ModuleType("tools")
    tools_package.__path__ = [str(TOOLS_DIR)]  # type: ignore[attr-defined]
    sys.modules["tools"] = tools_package
    _load_tool_module("tools.registry", "registry.py")


def test_prefers_local_hyperframes_binary(monkeypatch):
    _install_fake_tools_package()
    hyperframes_tool = _load_tool_module("tools.hyperframes_tool", "hyperframes_tool.py")

    monkeypatch.setattr(hyperframes_tool, "_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        hyperframes_tool,
        "_which",
        lambda name: "/usr/bin/npx" if name == "npx" else None,
    )
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/repo/node_modules/.bin/hyperframes")

    assert hyperframes_tool._resolve_hyperframes_command() == ["/repo/node_modules/.bin/hyperframes"]


def test_check_requirements_requires_node_22(monkeypatch):
    _install_fake_tools_package()
    hyperframes_tool = _load_tool_module("tools.hyperframes_tool", "hyperframes_tool.py")

    monkeypatch.setattr(
        hyperframes_tool,
        "_runtime_status",
        lambda: {
            "node_version": "v20.19.2",
            "ffmpeg": True,
            "ffprobe": True,
            "chrome": "/usr/bin/chromium",
            "hyperframes_command": ["hyperframes"],
        },
    )

    assert hyperframes_tool.check_hyperframes_requirements() is False


def test_handle_render_requires_project_dir():
    _install_fake_tools_package()
    hyperframes_tool = _load_tool_module("tools.hyperframes_tool", "hyperframes_tool.py")

    result = json.loads(hyperframes_tool._handle_hyperframes_video({"action": "render"}))

    assert result["success"] is False
    assert "project_dir" in result["error"]


def test_render_invokes_cli_with_expected_flags(monkeypatch, tmp_path):
    _install_fake_tools_package()
    hyperframes_tool = _load_tool_module("tools.hyperframes_tool", "hyperframes_tool.py")

    project_dir = tmp_path / "demo-project"
    project_dir.mkdir()
    output_path = project_dir / "renders" / "demo.mp4"

    monkeypatch.setattr(
        hyperframes_tool,
        "_resolve_project_dir",
        lambda value, *, create=False: project_dir,
    )
    monkeypatch.setattr(
        hyperframes_tool,
        "_run_hyperframes",
        lambda args, cwd=None, timeout_seconds=None: {
            "success": True,
            "command": ["/repo/node_modules/.bin/hyperframes", *args],
            "cwd": str(cwd),
            "stdout": "render ok",
            "stderr": "",
            "exit_code": 0,
        },
    )

    result = json.loads(
        hyperframes_tool._handle_hyperframes_video(
            {
                "action": "render",
                "project_dir": str(project_dir),
                "output_path": str(output_path),
                "fps": 60,
                "quality": "high",
                "format": "mp4",
                "use_docker": False,
            }
        )
    )

    assert result["success"] is True
    assert result["action"] == "render"
    assert "--output" in result["command"]
    assert str(output_path) in result["command"]
    assert "--fps" in result["command"]
    assert "60" in result["command"]
    assert "--quality" in result["command"]
    assert "high" in result["command"]
    assert result["cwd"] == str(project_dir)

