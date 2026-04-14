from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    "browser_window_not_found": {
        "zh": "未找到浏览器窗口，请确认容器内浏览器已启动",
        "en": "Browser window not found. Please ensure the browser is running in the container.",
    },
    "missing_text_param": {
        "zh": "缺少 text 参数",
        "en": "Missing text parameter",
    },
}


def t(key: str, locale: str = "zh", **kwargs: object) -> str:
    msgs = _MESSAGES.get(key, {})
    text = msgs.get(locale) or msgs.get("en") or msgs.get("zh") or key
    if kwargs:
        text = text.format(**kwargs)
    return text
