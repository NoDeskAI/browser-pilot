from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    "api_key_required": {
        "zh": "请先配置 API Key",
        "en": "Please configure your API Key first",
    },
    "message_empty": {
        "zh": "消息不能为空",
        "en": "Message cannot be empty",
    },
    "browser_window_not_found": {
        "zh": "未找到浏览器窗口，请确认容器内浏览器已启动",
        "en": "Browser window not found. Please ensure the browser is running in the container.",
    },
    "missing_text_param": {
        "zh": "缺少 text 参数",
        "en": "Missing text parameter",
    },
    "cycle_stop": {
        "zh": "操作未产生变化，已自动停止。请在浏览器中手动完成当前操作。",
        "en": "No change detected after repeated actions. Auto-stopped. Please complete the operation manually in the browser.",
    },
    "max_steps": {
        "zh": "⚠️ 已达到最大步数限制（{max_steps} 步），任务被中断。你可以继续发送指令让我接着完成。",
        "en": "⚠️ Reached the maximum step limit ({max_steps} steps). Task interrupted. You can send another instruction to continue.",
    },
}


def t(key: str, locale: str = "zh", **kwargs: object) -> str:
    msgs = _MESSAGES.get(key, {})
    text = msgs.get(locale) or msgs.get("en") or msgs.get("zh") or key
    if kwargs:
        text = text.format(**kwargs)
    return text
