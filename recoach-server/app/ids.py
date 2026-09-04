from __future__ import annotations

import secrets
import string
import time

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str) -> str:
    """生成可排序性不重要的短随机 ID，例如 ses_9f3k2a / turn_x7q1 / evt_m2z8。"""
    stamp = format(int(time.time() * 1000), "x")
    rand = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{prefix}_{stamp}{rand}"


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
