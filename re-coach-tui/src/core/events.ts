// 事件白名单与幂等写入（与后端 events.py 1:1 对齐）
import type { EventKind, LedgerEvent } from "../types.js";
import { newEventId } from "../ids.js";

export interface EventStore {
  logEvent(e: LedgerEvent): LedgerEvent;
}

export function makeEvent(
  args: {
    userId: string;
    turnId: string;
    kind: EventKind;
    payload: Record<string, unknown>;
    tokenCount?: number;
    latencyMs?: number;
  },
): LedgerEvent {
  return {
    // 走 crypto（见 ids.ts 的决定）：这条 id 会进 memories.source_event_ids
    // 并参与幂等匹配，不能用可预测且可能碰撞的 Math.random。
    id: newEventId(),
    userId: args.userId,
    turnId: args.turnId,
    kind: args.kind,
    payload: args.payload,
    tokenCount: args.tokenCount,
    latencyMs: args.latencyMs,
    createdAt: Date.now(),
  };
}
