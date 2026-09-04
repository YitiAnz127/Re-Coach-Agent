from __future__ import annotations

import json
from typing import Any, AsyncIterator


def frame(event: dict[str, Any]) -> str:
    """标准 SSE frame：event 名与 JSON 内 type 同名，便于代理与日志排查。"""
    event_type = event.get("type", "message")
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


async def encode(stream: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in stream:
        yield frame(event)
