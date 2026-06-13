"""VK polling platform adapter for Hermes Gateway.

The adapter follows the standalone hermes-vk-bridge approach: it polls
community dialogs with ``messages.getConversations`` and routes new inbound
messages through the normal Hermes Gateway pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import random
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
)

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"
MAX_VK_MESSAGE_LENGTH = 3500
DEFAULT_POLL_INTERVAL_SECONDS = 2.5

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
_AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".m4a"}
_APPROVE_RE = re.compile(
    r"^(?:/approve|approve|/код|код|/code|code)\s+(.+?)\s*$",
    re.IGNORECASE,
)


def _legacy_env_path() -> Path:
    return Path(
        os.getenv("VK_ENV_PATH")
        or str(get_hermes_home() / "scripts" / "vk_bridge.env")
    ).expanduser()


def load_vk_bridge_env() -> None:
    """Load the standalone bridge env file if VK vars are not in the env."""
    env_path = _legacy_env_path()
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning("[VK] failed to load env file %s: %s", env_path, exc)


def check_vk_requirements() -> bool:
    """VK uses only the Python standard library."""
    load_vk_bridge_env()
    return True


def _vk_api_sync(method: str, payload: dict[str, Any], token: str) -> Any:
    data = urllib.parse.urlencode(
        {**payload, "access_token": token, "v": VK_API_VERSION}
    ).encode()
    req = urllib.request.Request(
        "https://api.vk.com/method/" + method,
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")
    parsed = json.loads(body)
    if "error" in parsed:
        raise RuntimeError(f"VK API {method}: {parsed['error']}")
    return parsed.get("response")


def split_vk_text(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(empty response)"]
    if len(text) <= MAX_VK_MESSAGE_LENGTH:
        return [text]

    parts: list[str] = []
    rest = text
    while len(rest) > MAX_VK_MESSAGE_LENGTH:
        cut = rest.rfind("\n", 0, MAX_VK_MESSAGE_LENGTH)
        if cut < MAX_VK_MESSAGE_LENGTH // 2:
            cut = rest.rfind(" ", 0, MAX_VK_MESSAGE_LENGTH)
        if cut < MAX_VK_MESSAGE_LENGTH // 2:
            cut = MAX_VK_MESSAGE_LENGTH
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


def _download_url_bytes(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Hermes-VK-Gateway/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _safe_filename(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return value or fallback


def _attachment_url_and_name(att: dict[str, Any], index: int) -> tuple[str, str, str, MessageType]:
    typ = att.get("type") or "unknown"
    obj = att.get(typ) or {}
    title = obj.get("title") or obj.get("text") or f"{typ}_{int(time.time())}_{index}"

    if typ == "photo":
        sizes = obj.get("sizes") or []
        best = max(
            sizes,
            key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)),
            default={},
        )
        return best.get("url") or "", _safe_filename(title, f"photo_{index}.jpg"), ".jpg", MessageType.PHOTO

    if typ == "doc":
        url = obj.get("url") or ""
        suffix = Path(urllib.parse.urlparse(url).path).suffix or Path(str(title)).suffix
        return url, _safe_filename(title, f"document_{index}{suffix or '.bin'}"), suffix or ".bin", MessageType.DOCUMENT

    if typ == "audio_message":
        url = obj.get("link_ogg") or obj.get("link_mp3") or ""
        suffix = ".ogg" if obj.get("link_ogg") else ".mp3"
        return url, _safe_filename(title, f"voice_{index}{suffix}"), suffix, MessageType.VOICE

    return "", _safe_filename(title, f"{typ}_{index}.bin"), ".bin", MessageType.TEXT


def _cache_vk_attachment_sync(att: dict[str, Any], index: int) -> tuple[str, str, MessageType] | None:
    url, name, suffix, message_type = _attachment_url_and_name(att, index)
    if not url:
        return None

    data = _download_url_bytes(url)
    if message_type == MessageType.PHOTO:
        path = cache_image_from_bytes(data, suffix if suffix in _IMAGE_EXTS else ".jpg")
        media_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    elif message_type == MessageType.VOICE:
        path = cache_audio_from_bytes(data, suffix if suffix in _AUDIO_EXTS else ".ogg")
        media_type = mimetypes.guess_type(path)[0] or "audio/ogg"
    else:
        path = cache_document_from_bytes(data, name)
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return path, media_type, message_type


def _http_post_multipart_sync(url: str, fields: dict[str, str], files: dict[str, Path]) -> dict[str, Any]:
    boundary = "----hermesvk" + "".join(random.choice("abcdef0123456789") for _ in range(16))
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    for field, path in files.items():
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=b"".join(chunks),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def upload_vk_attachment_sync(token: str, peer_id: str, file_path: str) -> str:
    path = Path(file_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))

    if path.suffix.lower() in _IMAGE_EXTS:
        server = _vk_api_sync("photos.getMessagesUploadServer", {"peer_id": str(peer_id)}, token)
        uploaded = _http_post_multipart_sync(server["upload_url"], {}, {"photo": path})
        saved = _vk_api_sync(
            "photos.saveMessagesPhoto",
            {
                "photo": uploaded.get("photo", ""),
                "server": uploaded.get("server", ""),
                "hash": uploaded.get("hash", ""),
            },
            token,
        )
        photo = saved[0]
        access = ("_" + photo["access_key"]) if photo.get("access_key") else ""
        return f"photo{photo['owner_id']}_{photo['id']}{access}"

    server = _vk_api_sync("docs.getMessagesUploadServer", {"peer_id": str(peer_id)}, token)
    uploaded = _http_post_multipart_sync(server["upload_url"], {}, {"file": path})
    saved = _vk_api_sync("docs.save", {"file": uploaded.get("file", "")}, token)
    if isinstance(saved, list):
        doc = saved[0]
    else:
        doc = saved.get("doc", saved)
    access = ("_" + doc["access_key"]) if doc.get("access_key") else ""
    return f"doc{doc['owner_id']}_{doc['id']}{access}"


class VKAdapter(BasePlatformAdapter):
    """Polling VK adapter that routes community messages through Hermes Gateway."""

    MAX_MESSAGE_LENGTH = MAX_VK_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        load_vk_bridge_env()
        super().__init__(config, Platform.VK)
        self.token = config.token or config.extra.get("token") or os.getenv("VK_GROUP_TOKEN", "")
        self.poll_interval = float(
            config.extra.get("poll_interval")
            or os.getenv("VK_POLL_INTERVAL")
            or DEFAULT_POLL_INTERVAL_SECONDS
        )
        self.poll_filter = str(
            config.extra.get("poll_filter")
            or os.getenv("VK_POLL_FILTER")
            or "all"
        ).strip().lower()
        if self.poll_filter not in {"all", "unread", "important", "unanswered"}:
            self.poll_filter = "all"
        self.batch_size = int(config.extra.get("batch_size") or os.getenv("VK_POLL_BATCH_SIZE", "20"))
        self.state_path = Path(
            os.getenv("VK_GATEWAY_STATE_PATH")
            or str(get_hermes_home() / "state" / "vk_gateway_adapter_state.json")
        ).expanduser()
        self._poll_task: Optional[asyncio.Task] = None
        self._seen_ids: set[str] = set()
        self._load_state()

    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._seen_ids = {str(item) for item in data.get("seen_ids", [])[-5000:]}
        except Exception:
            self._seen_ids = set()

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"seen_ids": sorted(self._seen_ids)[-5000:], "ts": int(time.time())}
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as exc:
            logger.debug("[VK] state save failed: %s", exc)

    async def _vk_api(self, method: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(_vk_api_sync, method, payload, self.token)

    async def connect(self) -> bool:
        if not self.token:
            self._set_fatal_error("missing_token", "VK_GROUP_TOKEN is not configured", retryable=False)
            return False
        if not self._acquire_platform_lock("vk_group_token", self.token, "VK group token"):
            return False
        self._mark_connected()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="vk-poll-loop")
        logger.info(
            "[VK] adapter connected (poll_interval=%.1fs filter=%s)",
            self.poll_interval,
            self.poll_filter,
        )
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._release_platform_lock()
        self._mark_disconnected()

    async def _poll_loop(self) -> None:
        backoff = self.poll_interval
        while self._running:
            try:
                await self._poll_once()
                backoff = self.poll_interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[VK] poll error: %s", exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 1.7, 30.0)
                continue
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        resp = await self._vk_api(
            "messages.getConversations",
            {"count": str(self.batch_size), "filter": self.poll_filter},
        )
        for item in (resp or {}).get("items", []):
            msg = item.get("last_message") or {}
            if msg.get("out") == 1:
                continue

            msg_id = int(msg.get("id") or 0)
            peer_id = int(msg.get("peer_id") or 0)
            from_id = int(msg.get("from_id") or peer_id or 0)
            if msg_id <= 0 or peer_id <= 0:
                continue

            seen_key = f"{peer_id}:{msg_id}"
            if seen_key in self._seen_ids:
                continue
            self._seen_ids.add(seen_key)
            self._save_state()

            text = (msg.get("text") or "").strip()
            media_urls, media_types, media_message_type = await self._collect_attachments(msg)
            chat_type = "group" if peer_id >= 2_000_000_000 else "dm"
            message_type = self._message_type_for(text, media_message_type)
            source = self.build_source(
                chat_id=str(peer_id),
                user_id=str(from_id),
                user_name=str(from_id),
                chat_name=str(peer_id),
                chat_type=chat_type,
            )
            event = MessageEvent(
                text=text,
                message_type=message_type,
                source=source,
                raw_message=msg,
                message_id=str(msg_id),
                media_urls=media_urls,
                media_types=media_types,
            )
            logger.info("[VK] inbound peer=%s from=%s msg_id=%s text=%r", peer_id, from_id, msg_id, text[:80])
            await self.handle_message(event)
            await self._mark_read(peer_id)

    async def _collect_attachments(self, msg: dict[str, Any]) -> tuple[list[str], list[str], MessageType]:
        media_urls: list[str] = []
        media_types: list[str] = []
        strongest_type = MessageType.TEXT
        for index, attachment in enumerate(msg.get("attachments") or []):
            try:
                cached = await asyncio.to_thread(_cache_vk_attachment_sync, attachment, index)
            except Exception as exc:
                logger.warning("[VK] attachment cache failed type=%s: %s", attachment.get("type"), exc)
                continue
            if not cached:
                logger.debug("[VK] attachment type=%s has no direct download URL", attachment.get("type"))
                continue
            path, media_type, message_type = cached
            media_urls.append(path)
            media_types.append(media_type)
            if message_type == MessageType.PHOTO:
                strongest_type = MessageType.PHOTO
            elif strongest_type != MessageType.PHOTO and message_type == MessageType.VOICE:
                strongest_type = MessageType.VOICE
            elif strongest_type == MessageType.TEXT and message_type == MessageType.DOCUMENT:
                strongest_type = MessageType.DOCUMENT
        return media_urls, media_types, strongest_type

    @staticmethod
    def _message_type_for(text: str, media_message_type: MessageType) -> MessageType:
        if text.startswith("/"):
            return MessageType.COMMAND
        if media_message_type != MessageType.TEXT:
            return media_message_type
        return MessageType.TEXT

    async def _mark_read(self, peer_id: int) -> None:
        try:
            await self._vk_api("messages.markAsRead", {"peer_id": str(peer_id)})
        except Exception as exc:
            logger.debug("[VK] markAsRead failed peer=%s: %s", peer_id, exc)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        del reply_to, metadata
        try:
            first_message_id: Optional[str] = None
            raw = None
            base = random.randint(1, 2_000_000_000)
            for index, part in enumerate(split_vk_text(content)):
                raw = await self._vk_api(
                    "messages.send",
                    {
                        "peer_id": str(chat_id),
                        "random_id": str((base + index) % 2_147_483_647),
                        "message": part,
                    },
                )
                if first_message_id is None:
                    first_message_id = str(raw)
            return SendResult(success=True, message_id=first_message_id, raw_response=raw)
        except Exception as exc:
            logger.warning("[VK] send failed chat=%s: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def edit_message(self, chat_id: str, message_id: str, content: str) -> SendResult:
        try:
            raw = await self._vk_api(
                "messages.edit",
                {
                    "peer_id": str(chat_id),
                    "message_id": str(message_id),
                    "message": (content or "")[:MAX_VK_MESSAGE_LENGTH],
                },
            )
            return SendResult(success=bool(raw), message_id=str(message_id), raw_response=raw)
        except Exception as exc:
            logger.debug("[VK] edit failed chat=%s msg=%s: %s", chat_id, message_id, exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def send_typing(self, chat_id: str, metadata: Optional[dict[str, Any]] = None) -> None:
        del metadata
        try:
            await self._vk_api("messages.setActivity", {"peer_id": str(chat_id), "type": "typing"})
        except Exception:
            pass

    async def _send_attachment(self, chat_id: str, path: str, caption: Optional[str] = None) -> SendResult:
        try:
            attachment = await asyncio.to_thread(upload_vk_attachment_sync, self.token, str(chat_id), path)
            raw = await self._vk_api(
                "messages.send",
                {
                    "peer_id": str(chat_id),
                    "random_id": str(random.randint(1, 2_000_000_000)),
                    "message": caption or "",
                    "attachment": attachment,
                },
            )
            return SendResult(success=True, message_id=str(raw), raw_response=raw)
        except Exception as exc:
            logger.warning("[VK] attachment send failed chat=%s path=%s: %s", chat_id, path, exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        del reply_to, kwargs
        return await self._send_attachment(chat_id, image_path, caption)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        del reply_to, metadata
        try:
            suffix = Path(urllib.parse.urlparse(image_url).path).suffix.lower()
            if suffix not in _IMAGE_EXTS:
                suffix = ".jpg"
            data = await asyncio.to_thread(_download_url_bytes, image_url)
            local_path = await asyncio.to_thread(cache_image_from_bytes, data, suffix)
            return await self._send_attachment(chat_id, local_path, caption)
        except Exception as exc:
            logger.warning("[VK] image URL upload failed, falling back to text URL: %s", exc)
            text = f"{caption}\n{image_url}" if caption else image_url
            return await self.send(chat_id, text)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        del file_name, reply_to, kwargs
        return await self._send_attachment(chat_id, file_path, caption)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        del reply_to, kwargs
        return await self._send_attachment(chat_id, audio_path, caption)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        del reply_to, kwargs
        return await self._send_attachment(chat_id, video_path, caption)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {
            "name": str(chat_id),
            "type": "dm" if int(chat_id) < 2_000_000_000 else "group",
            "chat_id": str(chat_id),
        }

    @staticmethod
    def parse_approval_code(text: str) -> Optional[str]:
        match = _APPROVE_RE.match(text or "")
        return match.group(1).strip() if match else None
