"""澄清轮选项的「打字选择」识别。

问题背景（2026-09-18 用真实浏览器复现）：

澄清轮把选项渲染成 A/B/C/D/E 让对方点选。但用户很自然地会**直接打字回**
「1」或「A」——而编号方式与界面的字母对不上，服务端原本把这类输入当成一个
全新的问题，于是又抛出一整篇泛泛的讲解（实测打字回「1」产生 1438 字符，
而正常点选是 1184 字符且针对性完全不同）。

这里把「上一轮是澄清轮 + 本轮是纯选择符」识别为"选择第 N 项"，
替换成该选项完整的 followUp 文本，让用户打「1」也能得到点选一样的效果。

刻意保守：只有当上一轮**确实是带选项的澄清轮**时才生效，
避免把正常短消息误当成选项编号。
"""
from __future__ import annotations

import json
import re

# 允许「1」「A」「1.」「A、」「第2个」「选C」等常见写法
_PATTERNS = (
    re.compile(r"^(?:第)?\s*([1-9])\s*(?:个|项|条)?$"),
    re.compile(r"^(?:第)?\s*([A-Za-z])\s*(?:个|项|条)?$"),
    re.compile(r"^(?:选|选择)\s*([1-9A-Za-z])$"),
)


def _index_from_selector(raw: str) -> int | None:
    """把选择符解析成 0 基下标；不是选择符时返回 None。"""
    text = raw.strip().strip("。．.、,，)）]】 ")
    if not text or len(text) > 8:
        return None
    for pattern in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        token = match.group(1)
        if token.isdigit():
            value = int(token)
            return value - 1 if 1 <= value <= 9 else None
        # 字母：A/a -> 0，B/b -> 1 ...
        return ord(token.upper()) - ord("A")
    return None


def resolve_option_selection(user_text: str, options: list[dict]) -> str | None:
    """把选择符解析成对应选项的 followUp。

    options 为上一轮澄清轮的 clarificationOptions（含 label / followUp）。
    无法判定时返回 None，调用方应保持原输入不变。
    """
    if not options:
        return None

    # 1) 直接打出了选项文案本身（如「整体直觉」）
    normalized = user_text.strip().strip("。．.、,，")
    for option in options:
        label = str(option.get("label") or "").strip()
        if label and normalized == label:
            follow_up = str(option.get("followUp") or "").strip()
            if follow_up:
                return follow_up

    # 2) 编号或字母
    index = _index_from_selector(user_text)
    if index is None or not (0 <= index < len(options)):
        return None
    follow_up = str(options[index].get("followUp") or "").strip()
    return follow_up or None


def parse_options(presentation: dict | None) -> list[dict]:
    """从上一轮的 presentation 中取出澄清选项；没有则返回空。"""
    if not isinstance(presentation, dict):
        return []
    if presentation.get("mode") != "clarify":
        return []
    options = presentation.get("clarificationOptions")
    return [o for o in options if isinstance(o, dict)] if isinstance(options, list) else []


def load_presentation(raw_json: str | None) -> dict | None:
    if not raw_json:
        return None
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None
