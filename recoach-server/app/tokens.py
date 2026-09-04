from __future__ import annotations

import re

_CJK = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 1 字符 ≈ 1 token，其余按 4 字符 ≈ 1 token。

    只用于 Memory Capsule 预算与前端指标展示，不参与计费。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + max(1, other // 4)
