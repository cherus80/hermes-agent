"""Element scoring helpers shared by tools and the extension-facing protocol."""

from __future__ import annotations

import re
from typing import Any, Dict, List


_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


def _words(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "") if len(w) > 1]


_SYNONYMS = {
    "комментарий": ["comment", "reply", "ответ", "textbox", "textarea", "editor"],
    "коммент": ["comment", "reply", "ответ", "textbox", "textarea", "editor"],
    "comment": ["комментарий", "reply", "ответ", "textbox", "textarea", "editor"],
    "ответ": ["reply", "comment", "комментарий", "textbox", "textarea", "editor"],
    "поле": ["input", "field", "textbox", "textarea", "editor"],
    "field": ["поле", "input", "textbox", "textarea", "editor"],
    "кнопка": ["button", "submit", "click"],
    "button": ["кнопка", "submit", "click"],
    "опубликовать": ["publish", "post", "submit", "share", "отправить", "разместить"],
    "публикация": ["publish", "post", "submit", "share", "отправить", "разместить"],
    "publish": ["опубликовать", "post", "submit", "share", "отправить"],
    "send": ["отправить", "submit", "publish", "post"],
    "отправить": ["send", "submit", "publish", "post"],
    "поиск": ["search", "find"],
    "search": ["поиск", "find"],
    "название": ["title", "name"],
    "title": ["название", "name"],
    "описание": ["description", "caption", "bio"],
    "description": ["описание", "caption"],
    "загрузить": ["upload", "file", "attach"],
    "upload": ["загрузить", "file", "attach"],
}


def _expanded_words(text: str) -> List[str]:
    words = set(_words(text))
    for word in list(words):
        words.update(_SYNONYMS.get(word, []))
    return sorted(words)


def element_label(element: Dict[str, Any]) -> str:
    parts = [
        element.get("text"),
        element.get("ariaLabel"),
        element.get("placeholder"),
        element.get("label"),
        element.get("ariaDescription"),
        element.get("labelledBy"),
        element.get("describedBy"),
        element.get("name"),
        element.get("id"),
        element.get("testId"),
        element.get("autocomplete"),
        element.get("inputMode"),
        element.get("role"),
        element.get("type"),
        "button" if element.get("buttonLike") else "",
        "textbox" if element.get("textEntry") else "",
        element.get("nearText"),
    ]
    return " ".join(str(part) for part in parts if part)


def score_element(element: Dict[str, Any], target: str) -> float:
    label = element_label(element).lower()
    target_words = _expanded_words(target)
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
    button_like = bool(element.get("buttonLike")) or tag in {"button", "a"} or role in {"button", "link", "menuitem", "option", "tab", "switch", "checkbox", "radio"}
    text_entry = bool(element.get("textEntry")) or tag in {"input", "textarea", "select"} or role in {"textbox", "searchbox", "combobox"}
    if button_like or text_entry:
        score += 0.25
    if re.search(r"comment|reply|коммент|ответ|field|поле|text|текст|title|название|description|описание|search|поиск", target, re.I) and text_entry:
        score += 2.0
    if re.search(r"button|кнопка|click|нажми|publish|post|submit|send|share|опубликов|отправ|размест|save|сохран", target, re.I) and button_like:
        score += 2.0
    if re.search(r"upload|file|attach|загруз|файл|прикреп", target, re.I) and str(element.get("type") or "").lower() == "file":
        score += 3.0
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
    regions = snapshot.get("regions") or []
    return {
        "sessionId": snapshot.get("sessionId"),
        "tabId": snapshot.get("tabId"),
        "browserTab": snapshot.get("browserTab") or {},
        "extensionVersion": snapshot.get("extensionVersion"),
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "receivedAt": snapshot.get("receivedAt"),
        "textPreview": (snapshot.get("text") or "")[:1200],
        "elementCount": len(snapshot.get("elements") or []),
        "regionCount": len(regions),
        "regionPreview": [
            {
                "ref": region.get("ref"),
                "role": region.get("role"),
                "text": (region.get("text") or "")[:700],
                "links": region.get("links") or [],
                "numbers": region.get("numbers") or [],
                "controls": region.get("controls") or [],
            }
            for region in regions[:12]
            if isinstance(region, dict)
        ],
        "operatorSettings": snapshot.get("operatorSettings") or {},
        "authSignals": snapshot.get("authSignals") or {},
    }
