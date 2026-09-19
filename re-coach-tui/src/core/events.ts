// 事件白名单与幂等写入（与后端 events.py 1:1 对齐）
import type { EventKind, LedgerEvent } from "../types.js";

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
    id: `evt_${Math.random().toString(36).slice(2, 12)}`,
    userId: args.userId,
    turnId: args.turnId,
    kind: args.kind,
    payload: args.payload,
    tokenCount: args.tokenCount,
    latencyMs: args.latencyMs,
    createdAt: Date.now(),
  };
}
