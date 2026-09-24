"""运行时实例标识。

解决的问题：`turns.recover_stale_streaming` 在进程启动时把遗留的 `streaming`
Turn 标为 `error`，好让客户端用同一个 `clientTurnId` 重试时能原子认领。
如果不区分实例，多进程共享同一份 DB 时，**任何一个进程启动都会把别的进程
正在流式输出的 Turn 误标为 error**——那些轮次随后被客户端重试认领，造成
同一轮重复执行（双份 LLM 成本 + 两个写入者写同一行）。

标识为什么持久化在库里、而不是用主机名：容器重建会换主机名，而 DB 在挂载卷上。
用主机名的话，`docker compose up --force-recreate` 之后实例标识就变了，
本进程再也不会认领自己上一世遗留的 Turn——恢复能力静默失效，比不恢复更糟。
存进 DB 则"同一个库 = 同一个实例"，单实例部署依然保持"启动即恢复"。
"""
from __future__ import annotations

import socket

from .. import db
from ..config import get_settings
from ..ids import new_id

_META_KEY = "instance_id"

# 解析一次后缓存：每轮 Turn 建行都要用，不值得反复查库。
_cached: str | None = None


def _resolve() -> str:
    configured = get_settings().instance_id.strip()
    if configured:
        return configured

    row = db.query_one("SELECT value FROM runtime_meta WHERE key=?", (_META_KEY,))
    if row is not None and row["value"]:
        return str(row["value"])

    generated = f"{socket.gethostname()}-{new_id('inst')}"
    with db.tx() as conn:
        # 并发首启：两个进程同时生成时以先写入者为准（INSERT OR IGNORE + 回读）。
        conn.execute(
            "INSERT OR IGNORE INTO runtime_meta (key, value) VALUES (?,?)",
            (_META_KEY, generated),
        )
    row = db.query_one("SELECT value FROM runtime_meta WHERE key=?", (_META_KEY,))
    return str(row["value"]) if row is not None and row["value"] else generated


def current_instance_id() -> str:
    global _cached
    if _cached is None:
        _cached = _resolve()
    return _cached


def reset() -> None:
    """测试用：丢掉缓存，让下次调用重新解析（例如换了 DB 文件或改了配置）。"""
    global _cached
    _cached = None
