import { getDemoReply } from "../data/demo";
import type { AgentStreamEvent, TeachingRating, TurnPresentation } from "../types";
import {
  demoServiceMeta,
  parseServiceMeta,
  type ServiceMeta,
} from "./service-meta";
import { SseProtocolDecoder, SseProtocolError } from "./sse-protocol";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");
const demoMode = import.meta.env.VITE_DEMO_MODE === "true";

export interface SendTurnInput {
  sessionId: string;
  content: string;
  clientTurnId: string;
  signal?: AbortSignal;
}

export class AgentApiError extends Error {
  code: string;
  retryable: boolean;
  requestId?: string;

  constructor(message: string, code = "NETWORK", retryable = true, requestId?: string) {
    super(message);
    this.name = "AgentApiError";
    this.code = code;
    this.retryable = retryable;
    this.requestId = requestId;
  }
}

function wait(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function splitForStreaming(content: string) {
  return content.match(/[^。！？\n]+[。！？]?|\n+/g) ?? [content];
}

async function* streamDemoTurn(input: SendTurnInput): AsyncGenerator<AgentStreamEvent> {
  const reply = getDemoReply(input.content);
  const presentation = reply.presentation;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const turnId = `turn_${input.clientTurnId}`;

  await wait(reducedMotion ? 20 : 180, input.signal);
  yield {
    type: "turn.started",
    turnId,
    mode: presentation.mode,
    focus: presentation.focus,
    plan: presentation.plan,
  };

  if (reply.usesTool) {
    yield { type: "tool.started", turnId, tool: "run_micro_experiment" };
    await wait(reducedMotion ? 20 : 260, input.signal);
    yield { type: "tool.completed", turnId, tool: "run_micro_experiment" };
  }

  for (const chunk of splitForStreaming(reply.content)) {
    await wait(reducedMotion ? 4 : 54, input.signal);
    yield { type: "assistant.delta", turnId, delta: chunk };
  }

  yield { type: "turn.completed", turnId, presentation };
}

interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    requestId?: string;
  };
}

async function errorFromResponse(
  response: Response,
  fallbackMessage: string,
  fallbackCode: string,
) {
  let payload: ApiErrorPayload = {};
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    // Keep the user-safe fallback when the response is not JSON.
  }
  return new AgentApiError(
    payload.error?.message || fallbackMessage,
    payload.error?.code || fallbackCode,
    payload.error?.retryable ?? response.status >= 500,
    payload.error?.requestId,
  );
}

export async function* parseSseResponse(
  response: Response,
): AsyncGenerator<AgentStreamEvent> {
  if (!response.body) {
    throw new AgentApiError("服务端没有返回可读取的流。", "EMPTY_STREAM", true);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  const decoder = new SseProtocolDecoder();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const event of decoder.push(value)) yield event;
    }
    for (const event of decoder.finish()) yield event;
  } catch (error) {
    if (error instanceof SseProtocolError) {
      throw new AgentApiError(error.message, "PROTOCOL_ERROR", true);
    }
    throw error;
  } finally {
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}

export async function getServiceMeta(signal?: AbortSignal): Promise<ServiceMeta> {
  if (demoMode) return demoServiceMeta;

  const response = await fetch(`${apiBaseUrl}/meta`, {
    credentials: "include",
    signal,
  });
  if (!response.ok) {
    throw await errorFromResponse(
      response,
      "暂时无法读取模型服务信息。",
      "META_REQUEST_FAILED",
    );
  }
  return parseServiceMeta(await response.json());
}

export async function createSession(signal?: AbortSignal): Promise<string> {
  if (demoMode) {
    return `session_demo_${crypto.randomUUID()}`;
  }

  const response = await fetch(`${apiBaseUrl}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ locale: "zh-CN" }),
    signal,
  });

  if (!response.ok) {
    throw await errorFromResponse(
      response,
      "暂时无法创建学习会话。",
      "SESSION_CREATE_FAILED",
    );
  }

  const payload = (await response.json()) as { data?: { sessionId?: string } };
  if (!payload.data?.sessionId) {
    throw new AgentApiError("服务端没有返回 sessionId。", "INVALID_SESSION_RESPONSE", false);
  }

  return payload.data.sessionId;
}

/** 历史会话里的一轮，形状与服务端 GET /sessions/{id}/turns 对齐。 */
export interface RestoredTurn {
  turnId: string;
  /** 用户**原始**输入（编号选择不会被替换成长文本），用于如实还原对话。 */
  userText: string;
  assistantText: string;
  presentation: TurnPresentation;
  calibration?: TeachingRating | null;
  createdAt?: string;
}

/** 恢复会话历史。返回可直接渲染的轮次；会话不存在时抛 404 错误由调用方处理。 */
export async function fetchSessionTurns(
  sessionId: string,
  signal?: AbortSignal,
): Promise<RestoredTurn[]> {
  const response = await fetch(
    `${apiBaseUrl}/sessions/${encodeURIComponent(sessionId)}/turns`,
    // credentials 与其他调用保持一致：漏掉它会让恢复历史在需要凭证的部署下
    // 单独 401，而同一会话的发送/校准却正常——最难排查的一类不一致。
    { headers: { Accept: "application/json" }, credentials: "include", signal },
  );
  if (!response.ok) {
    const error = await errorFromResponse(
      response,
      "无法读取历史会话。",
      "SESSION_RESTORE_FAILED",
    );
    throw error;
  }
  const payload = (await response.json()) as {
    data?: { turns?: unknown };
  };
  const turns = payload.data?.turns;
  if (!Array.isArray(turns)) {
    throw new AgentApiError("历史会话数据格式不正确。", "INVALID_SESSION_RESPONSE", false);
  }
  // 逐条校验形状：后端版本不一致时宁可少渲染，也不要让坏数据进入 React state
  return turns.filter(isRestoredTurn) as RestoredTurn[];
}

function isRestoredTurn(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const t = value as Record<string, unknown>;
  return (
    typeof t.turnId === "string" &&
    typeof t.userText === "string" &&
    typeof t.assistantText === "string" &&
    typeof t.presentation === "object" &&
    t.presentation !== null &&
    (t.calibration === undefined || t.calibration === null ||
      t.calibration === "too_basic" || t.calibration === "just_right" || t.calibration === "too_fast")
  );
}

export async function* streamTurn(input: SendTurnInput): AsyncGenerator<AgentStreamEvent> {
  if (demoMode) {
    yield* streamDemoTurn(input);
    return;
  }

  const response = await fetch(`${apiBaseUrl}/sessions/${encodeURIComponent(input.sessionId)}/turns`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    credentials: "include",
    body: JSON.stringify({
      message: { content: input.content },
      clientTurnId: input.clientTurnId,
      locale: "zh-CN",
    }),
    signal: input.signal,
  });

  if (!response.ok) {
    throw await errorFromResponse(response, "讲解服务暂时不可用。", "TURN_REQUEST_FAILED");
  }

  yield* parseSseResponse(response);
}

export async function calibrateTurn(
  sessionId: string,
  turnId: string,
  rating: TeachingRating,
): Promise<void> {
  if (demoMode) throw new AgentApiError("请连接真实后端后再调整讲解起点。", "CALIBRATION_UNAVAILABLE", false);
  const response = await fetch(
    `${apiBaseUrl}/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/calibration`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ rating }),
    },
  );
  if (!response.ok) throw await errorFromResponse(response, "无法保存这次反馈。", "CALIBRATION_FAILED");
}


export interface FairAbFork {
  forkGroupId: string;
  sessionId: string;
  memoryMode: "on" | "off";
}

export async function createFairAbFork(
  sessionId: string,
  signal?: AbortSignal,
): Promise<{ forkGroupId: string; sourceSessionId: string; forks: FairAbFork[] }> {
  if (demoMode) {
    throw new AgentApiError("公平 Fork 需要连接真实后端。", "FORK_UNAVAILABLE", false);
  }
  const response = await fetch(`${apiBaseUrl}/sessions/${encodeURIComponent(sessionId)}/forks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({}),
    signal,
  });
  if (!response.ok) {
    throw await errorFromResponse(response, "暂时无法创建公平对照。", "FORK_CREATE_FAILED");
  }
  const payload = (await response.json()) as {
    data?: { forkGroupId?: string; sourceSessionId?: string; forks?: FairAbFork[] };
  };
  const data = payload.data;
  const forks = data?.forks;
  const validForks =
    Array.isArray(forks) &&
    forks.length === 2 &&
    forks.every(
      (fork) =>
        fork &&
        fork.forkGroupId === data?.forkGroupId &&
        typeof fork.sessionId === "string" &&
        (fork.memoryMode === "on" || fork.memoryMode === "off"),
    ) &&
    new Set(forks.map((fork) => fork.memoryMode)).size === 2;
  if (!data?.forkGroupId || !data.sourceSessionId || !validForks) {
    throw new AgentApiError("服务端返回异常，请刷新页面重试。", "INVALID_FORK_RESPONSE", false);
  }
  return {
    forkGroupId: data.forkGroupId,
    sourceSessionId: data.sourceSessionId,
    forks,
  };
}

export const agentClientConfig = {
  apiBaseUrl,
  demoMode,
};
