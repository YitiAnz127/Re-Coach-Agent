from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

_lock = threading.RLock()  # 可重入：tx() 内部允许同一线程再次读查询（如 fork 快照）
_conn: sqlite3.Connection | None = None
FTS5_AVAILABLE = True

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL,
  rule TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '*',
  concept_scope TEXT NOT NULL DEFAULT '*',
  proposition_scope TEXT NOT NULL DEFAULT '*',
  task_scope TEXT NOT NULL DEFAULT '*',
  polarity TEXT NOT NULL DEFAULT 'positive',
  evidence_kind TEXT NOT NULL,
  source_event_ids TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0.8,
  status TEXT NOT NULL DEFAULT 'active',
  superseded_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_scope
  ON memories (user_id, status, domain, concept_scope, task_scope);
CREATE INDEX IF NOT EXISTS idx_memories_prop
  ON memories (user_id, domain, concept_scope, proposition_scope);

CREATE TABLE IF NOT EXISTS concept_states (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  domain TEXT NOT NULL,
  concept TEXT NOT NULL,
  proposition TEXT NOT NULL,
  state TEXT NOT NULL,
  evidence_kind TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_concept_states
  ON concept_states (user_id, domain, concept);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  brief_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  client_turn_id TEXT NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  request_json TEXT NOT NULL,
  response_text TEXT NOT NULL DEFAULT '',
  presentation_json TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (session_id, client_turn_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  token_count INTEGER,
  latency_ms INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_turn ON events (session_id, turn_id, kind);
"""

# 轻量迁移记录让既有数据库和全新数据库都走同一初始化路径。
# P0 每个 Turn 的同类事件最多一条；P1 若引入多次工具调用，应升级为显式 dedupe_key。
MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "p0_retry_idempotency",
        """
        DELETE FROM messages
         WHERE rowid NOT IN (
           SELECT MAX(rowid) FROM messages GROUP BY turn_id, role
         );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_turn_role
          ON messages (turn_id, role);

        DELETE FROM events
         WHERE rowid NOT IN (
           SELECT MIN(rowid) FROM events GROUP BY turn_id, kind
         );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_events_turn_kind
          ON events (turn_id, kind);
        """,
    ),
    (
        2,
        "v11_fair_ab_forks",
        """
        CREATE TABLE IF NOT EXISTS session_forks (
          id TEXT PRIMARY KEY,
          fork_group_id TEXT NOT NULL,
          source_session_id TEXT NOT NULL,
          session_id TEXT NOT NULL UNIQUE,
          user_id TEXT NOT NULL,
          memory_mode TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_session_forks_group
          ON session_forks (fork_group_id, memory_mode);
        """,
    ),
    (
        3,
        "v11_fair_ab_snapshots",
        """
        ALTER TABLE session_forks ADD COLUMN memory_snapshot_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE session_forks ADD COLUMN concept_snapshot_json TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (
        4,
        "fork_content_snapshots",
        """
        ALTER TABLE session_forks ADD COLUMN memory_content_json TEXT;
        ALTER TABLE session_forks ADD COLUMN concept_content_json TEXT;
        """,
    ),
    (
        5,
        "concept_teaching_calibrations",
        """
        CREATE TABLE IF NOT EXISTS teaching_calibrations (
          turn_id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          domain TEXT NOT NULL,
          concept TEXT NOT NULL,
          rating TEXT NOT NULL,
          base_level TEXT NOT NULL,
          level TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_teaching_calibrations_scope
          ON teaching_calibrations (user_id, domain, concept, updated_at);
        """,
    ),
    (
        6,
        "turn_owner_instance",
        """
        ALTER TABLE turns ADD COLUMN owner_instance TEXT NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_turns_streaming_owner
          ON turns (status, owner_instance);

        CREATE TABLE IF NOT EXISTS runtime_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """,
    ),
)

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  rule, domain, concept_scope, proposition_scope, task_scope,
  content='memories', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, rule, domain, concept_scope, proposition_scope, task_scope)
  VALUES (new.rowid, new.rule, new.domain, new.concept_scope, new.proposition_scope, new.task_scope);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, rule, domain, concept_scope, proposition_scope, task_scope)
  VALUES ('delete', old.rowid, old.rule, old.domain, old.concept_scope, old.proposition_scope, old.task_scope);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, rule, domain, concept_scope, proposition_scope, task_scope)
  VALUES ('delete', old.rowid, old.rule, old.domain, old.concept_scope, old.proposition_scope, old.task_scope);
  INSERT INTO memories_fts(rowid, rule, domain, concept_scope, proposition_scope, task_scope)
  VALUES (new.rowid, new.rule, new.domain, new.concept_scope, new.proposition_scope, new.task_scope);
END;
"""


def _apply_migrations(conn: sqlite3.Connection) -> None:
    from .ids import now_iso

    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)",
            (version, name, now_iso()),
        )


def init(db_path: str | None = None) -> None:
    """初始化数据库、应用轻量迁移，并在 FTS5 不可用时降级为 LIKE 检索。"""
    global _conn, FTS5_AVAILABLE
    path = db_path or get_settings().db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _conn is not None:
            return
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.executescript(SCHEMA)
        _apply_migrations(_conn)
        try:
            _conn.executescript(FTS_SCHEMA)
            _conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
            _conn.execute("SELECT * FROM memories_fts LIMIT 0")
            FTS5_AVAILABLE = True
        except sqlite3.OperationalError:
            FTS5_AVAILABLE = False
        _conn.commit()


def reset_for_tests(db_path: str = ":memory:") -> None:
    """测试用：丢弃现有连接并按给定路径重建。"""
    global _conn, FTS5_AVAILABLE
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        FTS5_AVAILABLE = True
    init(db_path)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """线程安全的事务上下文。SQLite 是本地真相来源，所有写都走这里。"""
    assert _conn is not None, "db.init() 必须先于任何查询调用"
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    assert _conn is not None, "db.init() 必须先于任何查询调用"
    with _lock:
        return _conn.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    rows = query(sql, params)
    return rows[0] if rows else None
