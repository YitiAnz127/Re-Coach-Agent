"""并发 Turn 闸门。

作用：给"同时进行中的流式 Turn"设一个进程内上限，挡住
"开几千条 SSE 且不读响应" 这类把任务数与内存打满的 DoS，
同时避免所有流一起抢 `db._lock` 导致全局延迟崩塌。

实现上刻意用「显式计数 + 线程锁」而不是 `asyncio.Semaphore`：
Semaphore 在首次 await 时绑定事件循环，测试里多个 TestClient
会各自创建新循环，复用同一个 Semaphore 会抛 "bound to a different event loop"。
计数器不依赖事件循环，跨循环安全。

局限：进程内状态，多副本部署时每个副本各算一份上限。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_inflight = 0


def try_reserve(limit: int) -> bool:
    """尝试占用一个并发槽位。limit <= 0 表示不限制。"""
    global _inflight
    if limit <= 0:
        return True
    with _lock:
        if _inflight >= limit:
            return False
        _inflight += 1
        return True


def release(limit: int) -> None:
    """归还槽位。限制被调成 0（关闭）后不需要归还。"""
    global _inflight
    if limit <= 0:
        return
    with _lock:
        if _inflight > 0:
            _inflight -= 1


def inflight() -> int:
    with _lock:
        return _inflight


def reset() -> None:
    """测试用。"""
    global _inflight
    with _lock:
        _inflight = 0
