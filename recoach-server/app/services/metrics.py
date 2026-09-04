from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from .. import db


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _metric_block(values: list[float]) -> dict[str, float | None]:
    return {"count": len(values), "p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95)}


def summarize(*, user_id: str, session_id: str | None = None) -> dict[str, Any]:
    if session_id:
        rows = db.query("SELECT * FROM events WHERE user_id=? AND session_id=? ORDER BY created_at", (user_id, session_id))
    else:
        rows = db.query("SELECT * FROM events WHERE user_id=? ORDER BY created_at", (user_id,))

    completed_turns = {row["turn_id"] for row in rows if row["kind"] == "response_completed"}
    failed_turns = {row["turn_id"] for row in rows if row["kind"] == "turn_failed"}
    ttft: list[float] = []
    content_ttft: list[float] = []  # 新增：正文首字时间
    thinking_ttft: list[float] = []  # 新增：思考首字时间
    total_latency: list[float] = []
    memory_search: list[float] = []
    context_compile: list[float] = []
    input_tokens: list[float] = []
    modes: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    error_codes: Counter[str] = Counter()

    for row in rows:
        kind = row["kind"]
        if kind == "model_called" and row["latency_ms"] is not None:
            # latency_ms仍然是用户可见的首字时间（thinking或content，取决于谁先出现）
            ttft.append(float(row["latency_ms"]))
            
            # 从payload中提取细分的时间指标
            payload = json.loads(row["payload_json"] or "{}")
            if payload.get("provider"):
                providers[payload["provider"]] += 1
            
            # 新增：提取contentTtftMs和thinkingTtftMs
            if payload.get("contentTtftMs") and payload["contentTtftMs"] > 0:
                content_ttft.append(float(payload["contentTtftMs"]))
            if payload.get("thinkingTtftMs") and payload["thinkingTtftMs"] > 0:
                thinking_ttft.append(float(payload["thinkingTtftMs"]))
                
        elif kind == "response_completed" and row["latency_ms"] is not None:
            total_latency.append(float(row["latency_ms"]))
        elif kind == "memory_recalled" and row["latency_ms"] is not None:
            memory_search.append(float(row["latency_ms"]))
        elif kind == "context_compiled":
            if row["latency_ms"] is not None:
                context_compile.append(float(row["latency_ms"]))
            if row["token_count"] is not None:
                input_tokens.append(float(row["token_count"]))
        elif kind == "turn_started":
            payload = json.loads(row["payload_json"] or "{}")
            modes[str(payload.get("memoryMode", "default"))] += 1
        elif kind == "turn_failed":
            payload = json.loads(row["payload_json"] or "{}")
            error_codes[str(payload.get("error", "INTERNAL"))] += 1

    return {
        "turns": {"completed": len(completed_turns), "failed": len(failed_turns), "observed": len({row["turn_id"] for row in rows})},
        "latencyMs": _metric_block(total_latency),
        "ttftMs": _metric_block(content_ttft if content_ttft else ttft),  # 用户可见首字时间（包含thinking）
        "contentTtftMs": _metric_block(content_ttft),  # 新增：正文首字时间
        "thinkingTtftMs": _metric_block(thinking_ttft),  # 新增：思考首字时间
        "memorySearchMs": _metric_block(memory_search),
        "contextCompileMs": _metric_block(context_compile),
        "inputTokens": _metric_block(input_tokens),
        "memoryModeCounts": dict(modes),
        "providers": dict(providers),
        "errorsByType": dict(error_codes),
    }
