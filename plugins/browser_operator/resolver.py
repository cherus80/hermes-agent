"""Element scoring helpers shared by tools and the extension-facing protocol."""

from __future__ import annotations

import re
from typing import Any, Dict, List


_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


def _words(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "") if len(w) > 1]


def element_label(element: Dict[str, Any]) -> str:
    parts = [
        element.get("text"),
        element.get("ariaLabel"),
        element.get("placeholder"),
        element.get("label"),
        element.get("name"),
        element.get("id"),
        element.get("role"),
        element.get("type"),
        element.get("nearText"),
    ]
    return " ".join(str(part) for part in parts if part)


def score_element(element: Dict[str, Any], target: str) -> float:
    label = element_label(element).lower()
    target_words = _words(target)
    if not target_words:
        return 0.0

    score = 0.0
    if target.lower() in label:
        score += 3.0
    for word in target_words:
        if word in label:
            score += 1.0
    if element.get("visible") is False:
        score -= 2.0
    if element.get("enabled") is False:
        score -= 1.0
    tag = str(element.get("tag") or "").lower()
    role = str(element.get("role") or "").lower()
    if tag in {"button", "input", "textarea", "select", "a"} or role in {"button", "link", "textbox"}:
        score += 0.25
    return max(score, 0.0)


def find_candidates(snapshot: Dict[str, Any], target: str, limit: int = 8) -> List[Dict[str, Any]]:
    elements = snapshot.get("elements") or []
    scored = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        score = score_element(element, target)
        if score <= 0:
            continue
        item = dict(element)
        item["score"] = round(score, 3)
        item["label"] = element_label(element)[:400]
        scored.append(item)
    scored.sort(key=lambda item: item.get("score", 0), reverse=True)
    return scored[: max(1, min(limit, 25))]


def summarize_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sessionId": snapshot.get("sessionId"),
        "tabId": snapshot.get("tabId"),
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "receivedAt": snapshot.get("receivedAt"),
        "textPreview": (snapshot.get("text") or "")[:1200],
        "elementCount": len(snapshot.get("elements") or []),
        "authSignals": snapshot.get("authSignals") or {},
    }

