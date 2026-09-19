// Turn 事件（UI 订阅，替代后端 SSE）
import type { ClarificationOption, TurnPresentation } from "./types.js";

export type AgentTurnEvent =
  | { type: "turn.started"; turnId: string; mode: "clarify" | "explain"; focus: string; plan: string[] }
  | { type: "assistant.thinking"; turnId: string; delta: string }
  | { type: "assistant.delta"; turnId: string; delta: string }
  | { type: "turn.completed"; turnId: string; presentation: TurnPresentation }
  | { type: "turn.error"; turnId: string; code: string; message: string };

export interface AgentSession {
  id: string;
  memoryOn: boolean;
  isFork: boolean;
  memoryMode?: "on" | "off";
}
