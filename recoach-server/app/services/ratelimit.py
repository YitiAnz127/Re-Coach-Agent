"""进程内滑动窗口限流。

定位：拦住"单个身份无上限刷 LLM 计费端点"这一类成本放大攻击。
局限：状态在进程内存里，多副本部署下每个副本各算一份；
      需要严格全局配额时应换成 Redis 等共享后端。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)

# 最多同时跟踪多少个 key，防止 key 爆炸导致内存无界增长。
# 达到上限时会先回收过期项；仍满则**拒绝新 key**（返回限流），
# 而不是淘汰旧 key——见 check() 里的说明。
_MAX_KEYS = 10_000


def check(key: str, *, limit: int, window_seconds: float = 60.0) -> tuple[bool, float]:
    """登记一次请求。

    返回 (是否放行, 建议的 Retry-After 秒数)。limit <= 0 表示关闭限流。
    """
    if limit <= 0:
        return True, 0.0

    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        bucket = _hits.get(key)
        if bucket is None:
            # 新 key：先确认容量。满了且没有可回收的过期项时**保守拒绝**。
            # 不能改用"淘汰最旧的一项"来腾位置——那会让攻击者通过不断换 key
            # 把自己的计数桶挤掉，等于绕过限流。方向必须失败关闭。
            if len(_hits) >= _MAX_KEYS:
                _evict_stale(cutoff)
                if len(_hits) >= _MAX_KEYS:
                    return False, window_seconds
            bucket = _hits[key]

        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            retry_after = max(0.0, bucket[0] + window_seconds - now)
            return False, retry_after

        bucket.append(now)
        return True, 0.0


def _evict_stale(cutoff: float) -> None:
    """调用方必须已持有 _lock。清掉窗口内无请求的 key。"""
    for stale in [k for k, v in _hits.items() if not v or v[-1] < cutoff]:
        del _hits[stale]


def reset() -> None:
    """测试用：清空所有计数。"""
    with _lock:
        _hits.clear()
