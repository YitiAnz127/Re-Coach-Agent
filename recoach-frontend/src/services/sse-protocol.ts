import type { AgentStreamEvent, TurnPresentation } from "../types";

export class SseProtocolError extends Error {
  code: string;

  constructor(message: string, code: string) {
    super(message);
    this.name = "SseProtocolError";
    this.code = code;
  }
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isTurnMode(value: unknown) {
  return value === "clarify" || value === "explain" || value === "reflect";
}

function isDepth(value: unknown) {
  return (
    value === undefined ||
    value === "L0" ||
    value === "L1" ||
    value === "L2" ||
    value === "L3" ||
    value === "L4" ||
    value === "L5"
  );
}

function isMetrics(value: unknown) {
  if (!isRecord(value)) return false;
  return [
    "timeToFirstTokenMs",
    "memorySearchMs",
    "contextCompileMs",
    "memoryCapsuleTokens",
    "totalInputTokens",
  ].every((key) => typeof value[key] === "number" && Number.isFinite(value[key]));
}

function isObjectArray(value: unknown, predicate: (item: JsonRecord) => boolean) {
  return Array.isArray(value) && value.every((item) => isRecord(item) && predicate(item));
}

function isPresentation(value: unknown): value is TurnPresentation {
  if (!isRecord(value)) return false;
  if (!isTurnMode(value.mode) || !isDepth(value.depth)) return false;
  if (typeof value.focus !== "string" || !isStringArray(value.plan)) return false;
  if (!isMetrics(value.metrics)) return false;
  if (
    !isObjectArray(
      value.personalization,
      (item) =>
        typeof item.memoryId === "string" &&
        typeof item.label === "string" &&
        typeof item.scope === "string" &&
        typeof item.effect === "string",
    )
  ) {
    return false;
  }
  if (
    !isObjectArray(
      value.suggestedActions,
      (item) =>
        typeof item.id === "string" &&
        typeof item.label === "string" &&
        typeof item.prompt === "string",
    )
  ) {
    return false;
  }
  if (
    value.clarificationOptions !== undefined &&
    !isObjectArray(
      value.clarificationOptions,
      (item) =>
        typeof item.id === "string" &&
        typeof item.label === "string" &&
        typeof item.detail === "string" &&
        typeof item.followUp === "string",
    )
  ) {
    return false;
  }
  if (value.experiment !== undefined) {
    if (!isRecord(value.experiment)) return false;
    const experiment = value.experiment;
    if (
      typeof experiment.title !== "string" ||
      typeof experiment.description !== "string" ||
      typeof experiment.code !== "string" ||
      typeof experiment.takeaway !== "string" ||
      !isObjectArray(
        experiment.points,
        (item) =>
          typeof item.label === "string" &&
          typeof item.value === "number" &&
          Number.isFinite(item.value) &&
          typeof item.displayValue === "string",
      )
    ) {
      return false;
    }
  }
  if (value.retrospective !== undefined) {
    if (!isRecord(value.retrospective)) return false;
    if (
      !isStringArray(value.retrospective.connections) ||
      !isStringArray(value.retrospective.openQuestions) ||
      !isStringArray(value.retrospective.approach)
    ) {
      return false;
    }
  }
  if (value.outputVerification !== undefined) {
    if (!isRecord(value.outputVerification)) return false;
    if (
      !["passed", "partial", "unknown"].includes(value.outputVerification.status as string) ||
      !isStringArray(value.outputVerification.checks) ||
      !isStringArray(value.outputVerification.violations)
    ) {
      return false;
    }
  }
  return true;
}

function isAgentStreamEvent(value: unknown): value is AgentStreamEvent {
  if (!isRecord(value) || typeof value.type !== "string" || typeof value.turnId !== "string") {
    return false;
  }
  switch (value.type) {
    case "turn.started":
      return (
        isTurnMode(value.mode) &&
        typeof value.focus === "string" &&
        isStringArray(value.plan)
      );
    case "assistant.delta":
      return typeof value.delta === "string";
    case "assistant.thinking":
      return typeof value.delta === "string";
    case "tool.started":
    case "tool.completed":
      // P1 预留：后端当前不发送 tool 事件（微型实验工具尚未实现），
      // 此分支为契约预留，保持前端解码器与未来协议一致。
      return value.tool === "run_micro_experiment";
    case "turn.completed":
      return isPresentation(value.presentation);
    case "turn.error":
      return (
        typeof value.code === "string" &&
        typeof value.message === "string" &&
        typeof value.retryable === "boolean" &&
        (value.requestId === undefined || typeof value.requestId === "string")
      );
    default:
      return false;
  }
}

function parseFrame(rawFrame: string): AgentStreamEvent | null {
  let data = "";
  for (const line of rawFrame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue;
    if (!line.startsWith("data:")) continue;
    const next = line.slice(5).trimStart();
    data = data ? `${data}\n${next}` : next;
  }
  if (!data) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    throw new SseProtocolError("服务端返回了无法解析的事件。", "INVALID_SSE_EVENT");
  }

  if (!isAgentStreamEvent(parsed)) {
    throw new SseProtocolError("服务端返回了不完整的事件。", "INVALID_SSE_EVENT");
  }
  return parsed;
}

function isTerminal(event: AgentStreamEvent) {
  return event.type === "turn.completed" || event.type === "turn.error";
}

/**
 * 未闭合帧的缓冲上限（字符数）。
 *
 * 服务端（或中间代理）若持续发送不含空行的数据，buffer 会无界增长，
 * 最终打爆浏览器内存。正常事件远小于这个上限，触到即为异常流。
 */
const MAX_PENDING_FRAME_CHARS = 1_000_000;

export class SseProtocolDecoder {
  private buffer = "";
  private terminalSeen = false;

  push(chunk: string): AgentStreamEvent[] {
    if (!chunk) return [];
    this.buffer += chunk;
    const frames = this.buffer.split(/\r?\n\r?\n/);
    this.buffer = frames.pop() ?? "";
    if (this.buffer.length > MAX_PENDING_FRAME_CHARS) {
      this.buffer = "";
      throw new SseProtocolError(
        "服务端事件帧超过大小上限，已中止读取。",
        "FRAME_TOO_LARGE",
      );
    }
    return frames.flatMap((frame) => this.consumeFrame(frame));
  }

  finish(finalChunk = ""): AgentStreamEvent[] {
    const events = this.push(finalChunk);
    if (this.buffer.trim()) {
      events.push(...this.consumeFrame(this.buffer));
      this.buffer = "";
    }
    if (!this.terminalSeen) {
      throw new SseProtocolError("流在完成事件到达前结束。", "MISSING_TERMINAL_EVENT");
    }
    return events;
  }

  private consumeFrame(rawFrame: string): AgentStreamEvent[] {
    const event = parseFrame(rawFrame);
    if (!event) return [];
    if (this.terminalSeen) {
      throw new SseProtocolError("终止事件后又收到了额外事件。", "EVENT_AFTER_TERMINAL");
    }
    if (isTerminal(event)) this.terminalSeen = true;
    return [event];
  }
}
