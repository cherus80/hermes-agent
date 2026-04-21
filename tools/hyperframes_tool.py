#!/usr/bin/env python3
"""HyperFrames video rendering tool for Hermes Agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hermes_constants import display_hermes_home, get_hermes_home, get_subprocess_home
from tools.registry import registry, tool_error, tool_result


MIN_NODE_MAJOR = 22
DEFAULT_TIMEOUT_SECONDS = 900
VALID_ACTIONS = {"doctor", "init", "compositions", "render"}
VALID_EXAMPLES = {"blank", "warm-grain", "play-mode", "swiss-grid", "vignelli"}
VALID_FORMATS = {"mp4", "mov", "webm"}
VALID_QUALITIES = {"draft", "standard", "high"}
VALID_FPS = {24, 30, 60}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _which(binary_name: str) -> str | None:
    return shutil.which(binary_name)


def _extract_major_version(version_text: str | None) -> int | None:
    if not version_text:
        return None
    match = re.search(r"(\d+)", version_text)
    if not match:
        return None
    return int(match.group(1))


def _read_command_output(command: list[str], timeout_seconds: int = 15) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_subprocess_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output or None


def _resolve_hyperframes_command() -> list[str] | None:
    explicit = os.getenv("HYPERFRAMES_BIN", "").strip()
    if explicit:
        return [explicit]

    local_bin = _repo_root() / "node_modules" / ".bin" / "hyperframes"
    if local_bin.exists():
        return [str(local_bin)]

    installed = _which("hyperframes")
    if installed:
        return [installed]

    npx = _which("npx")
    if npx:
        return [npx, "--yes", "hyperframes"]
    return None


def _resolve_chrome_binary() -> str | None:
    for env_name in ("PUPPETEER_EXECUTABLE_PATH", "CHROME_BIN", "CHROMIUM_BIN"):
        explicit = os.getenv(env_name, "").strip()
        if explicit and Path(explicit).exists():
            return explicit
    for binary_name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        resolved = _which(binary_name)
        if resolved:
            return resolved
    return None


def _runtime_status() -> dict[str, Any]:
    hyperframes_command = _resolve_hyperframes_command()
    node_version = _read_command_output(["node", "--version"])
    return {
        "node_version": node_version,
        "node_major": _extract_major_version(node_version),
        "ffmpeg": bool(_which("ffmpeg")),
        "ffprobe": bool(_which("ffprobe")),
        "chrome": _resolve_chrome_binary(),
        "hyperframes_command": hyperframes_command,
    }


def check_hyperframes_requirements() -> bool:
    status = _runtime_status()
    return bool(
        status.get("node_major") is not None
        and status["node_major"] >= MIN_NODE_MAJOR
        and status["ffmpeg"]
        and status["ffprobe"]
        and status["chrome"]
        and status["hyperframes_command"]
    )


def _workspace_root() -> Path:
    workspace = get_hermes_home() / "workspace" / "hyperframes"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _slugify_project_name(project_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(project_name).strip()).strip("-").lower()
    if not slug:
        raise ValueError("project_name must contain at least one letter or number")
    return slug


def _resolve_project_dir(project_dir: str, *, create: bool = False) -> Path:
    base_dir = _workspace_root().resolve()
    candidate = Path(project_dir).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate

    resolved = candidate.resolve(strict=False)
    if resolved != base_dir and base_dir not in resolved.parents:
        raise ValueError(
            f"project_dir must stay inside {base_dir}. "
            f"Use a relative path under {display_hermes_home()}/workspace/hyperframes."
        )

    if create:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    elif not resolved.exists():
        raise ValueError(f"project_dir does not exist: {resolved}")
    return resolved


def _resolve_existing_file(path_value: str | None, field_name: str) -> Path | None:
    if not path_value:
        return None
    resolved = Path(path_value).expanduser().resolve(strict=False)
    if not resolved.exists():
        raise ValueError(f"{field_name} does not exist: {resolved}")
    return resolved


def _resolve_output_path(project_dir: Path, output_path: str | None, video_format: str) -> Path:
    if output_path:
        candidate = Path(output_path).expanduser()
        if not candidate.is_absolute():
            candidate = project_dir / candidate
    else:
        candidate = project_dir / "renders" / f"{project_dir.name}.{video_format}"

    resolved = candidate.resolve(strict=False)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    subprocess_home = get_subprocess_home()
    if subprocess_home:
        env["HOME"] = subprocess_home

    chrome_binary = _resolve_chrome_binary()
    if chrome_binary:
        env.setdefault("CHROME_BIN", chrome_binary)
        env.setdefault("CHROMIUM_BIN", chrome_binary)
        env.setdefault("PUPPETEER_EXECUTABLE_PATH", chrome_binary)

    env.setdefault("HYPERFRAMES_DISABLE_TELEMETRY", "1")
    return env


def _run_hyperframes(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    command = _resolve_hyperframes_command()
    if not command:
        return {
            "success": False,
            "error": "HyperFrames CLI is not installed. Install it or set HYPERFRAMES_BIN.",
        }

    full_command = command + args
    try:
        completed = subprocess.run(
            full_command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "error": f"HyperFrames command timed out after {timeout_seconds} seconds",
            "command": full_command,
            "cwd": str(cwd) if cwd else None,
            "stdout": (exc.stdout or "").strip(),
            "stderr": (exc.stderr or "").strip(),
        }
    except FileNotFoundError as exc:
        return {
            "success": False,
            "error": f"Failed to execute HyperFrames command: {exc}",
            "command": full_command,
            "cwd": str(cwd) if cwd else None,
        }

    return {
        "success": completed.returncode == 0,
        "command": full_command,
        "cwd": str(cwd) if cwd else None,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
        "exit_code": completed.returncode,
    }


def _handle_doctor() -> str:
    status = _runtime_status()
    result = _run_hyperframes(["doctor"], cwd=_workspace_root(), timeout_seconds=180)
    if "success" not in result:
        result["success"] = False
    result["action"] = "doctor"
    result["requirements"] = status
    return json.dumps(result, ensure_ascii=False)


def _handle_init(args: dict[str, Any]) -> str:
    project_name = str(args.get("project_name", "")).strip()
    if not project_name:
        return tool_error("project_name is required for action='init'", success=False)

    example = str(args.get("example", "blank")).strip() or "blank"
    if example not in VALID_EXAMPLES:
        return tool_error(
            f"Unsupported example '{example}'. Supported examples: {', '.join(sorted(VALID_EXAMPLES))}.",
            success=False,
        )

    project_dir_value = args.get("project_dir") or _slugify_project_name(project_name)
    try:
        project_dir = _resolve_project_dir(str(project_dir_value), create=True)
        video_path = _resolve_existing_file(args.get("video_path"), "video_path")
        audio_path = _resolve_existing_file(args.get("audio_path"), "audio_path")
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    command = ["init", str(project_dir), "--example", example, "--skip-skills"]
    if video_path:
        command.extend(["--video", str(video_path)])
    if audio_path:
        command.extend(["--audio", str(audio_path)])
    if not video_path and not audio_path:
        command.append("--skip-transcribe")

    result = _run_hyperframes(command, cwd=_workspace_root(), timeout_seconds=300)
    payload = {
        **result,
        "action": "init",
        "project_dir": str(project_dir),
        "workspace_root": str(_workspace_root()),
    }
    return json.dumps(payload, ensure_ascii=False)


def _handle_compositions(args: dict[str, Any]) -> str:
    project_dir_value = str(args.get("project_dir", "")).strip()
    if not project_dir_value:
        return tool_error("project_dir is required for action='compositions'", success=False)

    try:
        project_dir = _resolve_project_dir(project_dir_value, create=False)
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    result = _run_hyperframes(["compositions", "--json"], cwd=project_dir, timeout_seconds=120)
    payload = {**result, "action": "compositions", "project_dir": str(project_dir)}
    return json.dumps(payload, ensure_ascii=False)


def _handle_render(args: dict[str, Any]) -> str:
    project_dir_value = str(args.get("project_dir", "")).strip()
    if not project_dir_value:
        return tool_error("project_dir is required for action='render'", success=False)

    video_format = str(args.get("format", "mp4")).strip().lower()
    if video_format not in VALID_FORMATS:
        return tool_error(
            f"Unsupported format '{video_format}'. Supported formats: {', '.join(sorted(VALID_FORMATS))}.",
            success=False,
        )

    quality = str(args.get("quality", "standard")).strip().lower()
    if quality not in VALID_QUALITIES:
        return tool_error(
            f"Unsupported quality '{quality}'. Supported qualities: {', '.join(sorted(VALID_QUALITIES))}.",
            success=False,
        )

    fps = int(args.get("fps", 30))
    if fps not in VALID_FPS:
        return tool_error(f"Unsupported fps '{fps}'. Supported values: 24, 30, 60.", success=False)

    try:
        project_dir = _resolve_project_dir(project_dir_value, create=False)
        source_file = _resolve_existing_file(args.get("source_file"), "source_file")
        output_path = _resolve_output_path(project_dir, args.get("output_path"), video_format)
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    command = ["render"]
    if source_file:
        command.append(str(source_file))
    command.extend(["--output", str(output_path), "--format", video_format, "--fps", str(fps), "--quality", quality])

    crf = args.get("crf")
    if crf is not None:
        command.extend(["--crf", str(crf)])
    video_bitrate = str(args.get("video_bitrate", "")).strip()
    if video_bitrate:
        command.extend(["--video-bitrate", video_bitrate])
    workers = args.get("workers")
    if workers not in (None, ""):
        command.extend(["--workers", str(workers)])
    max_concurrent_renders = args.get("max_concurrent_renders")
    if max_concurrent_renders not in (None, ""):
        command.extend(["--max-concurrent-renders", str(max_concurrent_renders)])
    if args.get("use_gpu"):
        command.append("--gpu")
    if args.get("use_docker"):
        command.append("--docker")
    if args.get("quiet"):
        command.append("--quiet")

    timeout_seconds = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    result = _run_hyperframes(command, cwd=project_dir, timeout_seconds=timeout_seconds)
    payload = {
        **result,
        "action": "render",
        "project_dir": str(project_dir),
        "output_path": str(output_path),
        "source_file": str(source_file) if source_file else None,
    }
    return json.dumps(payload, ensure_ascii=False)


def _handle_hyperframes_video(args: dict[str, Any], **_: Any) -> str:
    action = str(args.get("action", "render")).strip().lower()
    if action not in VALID_ACTIONS:
        return tool_error(
            f"Unknown action '{action}'. Supported actions: {', '.join(sorted(VALID_ACTIONS))}.",
            success=False,
        )

    if action == "doctor":
        return _handle_doctor()
    if action == "init":
        return _handle_init(args)
    if action == "compositions":
        return _handle_compositions(args)
    return _handle_render(args)


HYPERFRAMES_VIDEO_SCHEMA = {
    "name": "hyperframes_video",
    "description": (
        "Create and render HyperFrames video projects inside Hermes. "
        f"Projects live under {display_hermes_home()}/workspace/hyperframes by default. "
        "Typical flow: action='doctor' to verify the runtime, action='init' to scaffold a project, "
        "use file tools to edit the generated HTML/CSS/JS composition files, action='compositions' to inspect available compositions, "
        "and action='render' to export MP4/MOV/WebM output."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(VALID_ACTIONS),
                "description": "doctor, init, compositions, or render",
            },
            "project_name": {
                "type": "string",
                "description": "Project name for action='init'. It is converted into a filesystem-safe directory name.",
            },
            "project_dir": {
                "type": "string",
                "description": "Project directory relative to the HyperFrames workspace, or an absolute path inside it.",
            },
            "example": {
                "type": "string",
                "enum": sorted(VALID_EXAMPLES),
                "description": "Starter template for action='init'.",
                "default": "blank",
            },
            "video_path": {
                "type": "string",
                "description": "Optional existing video file to import during action='init'.",
            },
            "audio_path": {
                "type": "string",
                "description": "Optional existing audio file to import during action='init'.",
            },
            "source_file": {
                "type": "string",
                "description": "Optional HTML composition file for action='render'. If omitted, HyperFrames renders the current project root.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional output file path for action='render'. Relative paths are resolved inside project_dir.",
            },
            "format": {
                "type": "string",
                "enum": sorted(VALID_FORMATS),
                "default": "mp4",
            },
            "fps": {
                "type": "integer",
                "enum": sorted(VALID_FPS),
                "default": 30,
            },
            "quality": {
                "type": "string",
                "enum": sorted(VALID_QUALITIES),
                "default": "standard",
            },
            "crf": {
                "type": "integer",
                "description": "Optional FFmpeg CRF override for render.",
            },
            "video_bitrate": {
                "type": "string",
                "description": "Optional bitrate override for render, e.g. 10M or 5000k.",
            },
            "workers": {
                "type": "string",
                "description": "Optional parallel workers override for render.",
            },
            "max_concurrent_renders": {
                "type": "integer",
                "description": "Optional max concurrent renders override for render.",
            },
            "use_gpu": {
                "type": "boolean",
                "description": "Enable GPU encoding for action='render' when available.",
                "default": False,
            },
            "use_docker": {
                "type": "boolean",
                "description": "Ask HyperFrames to use Docker mode for deterministic renders.",
                "default": False,
            },
            "quiet": {
                "type": "boolean",
                "description": "Reduce CLI output for action='render'.",
                "default": False,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Optional custom timeout for action='render'.",
                "default": DEFAULT_TIMEOUT_SECONDS,
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="hyperframes_video",
    toolset="video",
    schema=HYPERFRAMES_VIDEO_SCHEMA,
    handler=_handle_hyperframes_video,
    check_fn=check_hyperframes_requirements,
    requires_env=[],
    is_async=False,
    emoji="🎬",
)
