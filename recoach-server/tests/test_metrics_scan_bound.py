"""metrics 汇总的扫描上界回归测试。

背景：events 表随使用无界增长（每个 Turn 约 6 条），而 summarize 会把命中的
每一行读进内存并逐条 json.loads。原实现没有 LIMIT，单用户长期使用即可把
这个只读端点变成内存放大面（1g mem_limit 下足以让容器被杀）。
"""
from __future__ import annotations

from app import db
from app.services import events as event_service
from app.services import metrics


def _seed_turns(user_id: str, count: int, *, tag: str = "") -> None:
    """按顺序写入 count 轮，每轮两条事件（started + completed）。

    顺序即插入顺序，因此 rowid 的先后可用来断言"取到的是最近的一批"。

    tag 用于区分用户：事件幂等键是**全局**的 (turn_id, kind)（见
    ux_events_turn_kind），两个用户复用同一 turn_id 时后写的事件会被
    当作重复而丢弃。真实 turn_id 由服务端生成且全局唯一，这里手动造数据
    需要自己保证不撞。
    """
    for index in range(count):
        turn_id = f"turn_{tag}{index:03d}"
        event_service.log_event(
            user_id=user_id, session_id="ses_1", turn_id=turn_id,
            mode="explain", kind="turn_started", payload={"memoryMode": "default"},
        )
        event_service.log_event(
            user_id=user_id, session_id="ses_1", turn_id=turn_id,
            mode="explain", kind="response_completed",
            payload={"chars": 10}, latency_ms=100 + index,
        )


def test_summary_counts_everything_within_the_scan_limit(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "metrics_full.db"))
    _seed_turns("metrics_user", 30)

    # 上界高于事件总数：行为与"全表"一致，聚合结果不受窗口影响。
    monkeypatch.setattr(metrics, "EVENTS_SCAN_LIMIT", 1_000)
    summary = metrics.summarize(user_id="metrics_user")

    assert summary["turns"]["observed"] == 30
    assert summary["turns"]["completed"] == 30
    assert summary["latencyMs"]["count"] == 30


def test_summary_scans_only_the_most_recent_events(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "metrics_bounded.db"))
    _seed_turns("metrics_user", 30)

    # 60 条事件里只允许扫描最近 20 条 → 覆盖最后 10 轮（每轮 2 条）。
    # 必须有界；断言取到的是**最近**的批次而不是最早的批次。
    monkeypatch.setattr(metrics, "EVENTS_SCAN_LIMIT", 20)
    summary = metrics.summarize(user_id="metrics_user")

    assert summary["turns"]["observed"] == 10
    assert summary["turns"]["completed"] == 10
    assert summary["latencyMs"]["count"] == 10
    # 最近 10 轮的 latency 是 120..129（index 20..29），取最大值可区分新旧批次
    assert summary["latencyMs"]["p95"] == 129


def test_summary_is_scoped_per_user(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "metrics_scope.db"))
    _seed_turns("user_a", 4, tag="a_")
    _seed_turns("user_b", 7, tag="b_")

    monkeypatch.setattr(metrics, "EVENTS_SCAN_LIMIT", 1_000)

    assert metrics.summarize(user_id="user_a")["turns"]["observed"] == 4
    assert metrics.summarize(user_id="user_b")["turns"]["observed"] == 7
