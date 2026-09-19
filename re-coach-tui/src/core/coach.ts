// 主 Coach 流式输出（与后端 coach.py 1:1 对齐；用原生 fetch 替代 httpx）
import type { ResolvedTask, StreamDelta } from "../types.js";
import { resolveProvider, type AppConfig } from "../config.js";

export interface CoachMeta {
  provider: string;
  model: string;
  /** 降级发生时记录"原本想用谁"——降级后 provider 会被改成 template，否则无从追溯。 */
  requestedProvider: string;
  requestedModel: string;
  ttftMs: number;
  thinkingTtftMs: number;
  contentTtftMs: number;
  fallback: boolean;
  /** 降级的粗粒度原因：AUTH / QUOTA / TIMEOUT / NETWORK / PROVIDER_ERROR / HTTP_ERROR / ERROR。 */
  fallbackReason: string;
  truncated: boolean;
  continuationCount: number;
  thinkingTokens: number;
  actualThinkingChars: number;
  suppressThinking: boolean;
}

/**
 * provider 返回非 2xx 时的结构化错误。
 *
 * 刻意**不把上游响应体放进 message**：原文可能含内部细节，
 * 一旦被日志或界面带出去就是泄露。诊断只需要状态码，
 * 面向用户的说明由 classifyProviderFailure 的类别决定。
 */
export class ProviderHttpError extends Error {
  readonly status: number;

  constructor(provider: string, status: number) {
    super(`${provider} HTTP ${status}`);
    this.name = "ProviderHttpError";
    this.status = status;
  }
}

/** 把 provider 失败归类。只返回类别，绝不带出异常原文（可能含 URL / 响应体）。 */
export function classifyProviderFailure(err: unknown): string {
  if (err && typeof err === "object" && "status" in err) {
    const status = Number((err as { status?: unknown }).status);
    if (status === 401 || status === 403) return "AUTH";
    if (status === 429) return "QUOTA";
    if (status >= 500) return "PROVIDER_ERROR";
    if (status > 0) return "HTTP_ERROR";
  }
  const name = err instanceof Error ? err.name : "";
  if (name === "AbortError" || /timeout/i.test(String(name))) return "TIMEOUT";
  if (err instanceof TypeError) return "NETWORK"; // fetch 的网络层失败
  return "ERROR";
}

export function newCoachMeta(): CoachMeta {
  return {
    provider: "template",
    model: "template",
    requestedProvider: "",
    requestedModel: "",
    ttftMs: 0,
    thinkingTtftMs: 0,
    contentTtftMs: 0,
    fallback: false,
    fallbackReason: "",
    truncated: false,
    continuationCount: 0,
    thinkingTokens: 0,
    actualThinkingChars: 0,
    suppressThinking: false,
  };
}

function estimateTokensChinese(text: string): number {
  const chineseChars = Array.from(text).filter((c) => c >= "\u4e00" && c <= "\u9fff").length;
  const otherChars = text.length - chineseChars;
  return Math.round(chineseChars / 1.2 + otherChars / 4);
}

// 单行未终止时的缓冲上限。恶意/异常端点可以持续发送不含换行的数据，
// AbortController 只限时间不限字节，没有这个上限就能在超时前打爆堆内存。
const MAX_PENDING_LINE = 1_000_000;

async function* streamDataLines(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let index: number;
      while ((index = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (line.startsWith("data:")) yield line.slice(5).trim();
      }
      if (buffer.length > MAX_PENDING_LINE) {
        throw new Error(`SSE 单行超过 ${MAX_PENDING_LINE} 字节上限，已中止读取。`);
      }
      if (done) {
        const line = buffer.trim();
        if (line.startsWith("data:")) yield line.slice(5).trim();
        return;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}

interface ChatChunk {
  choices?: Array<{
    finish_reason?: string;
    delta?: { reasoning_content?: string; content?: string };
  }>;
}

async function* streamChatCompletions(
  cfg: AppConfig,
  system: string,
  user: string,
  meta: CoachMeta,
  opts: {
    baseUrl: string;
    apiKey: string;
    model: string;
    extraPayload?: Record<string, unknown>;
    thinkingCharLimit?: number | null;
  },
): AsyncGenerator<StreamDelta> {
  const url = `${opts.baseUrl.replace(/\/+$/, "")}/chat/completions`;
  const payload: Record<string, unknown> = {
    model: opts.model,
    stream: true,
    max_tokens: cfg.llmMaxTokens,
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
  };
  if (opts.extraPayload) Object.assign(payload, opts.extraPayload);
  const started = performance.now();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), cfg.llmTimeoutMs);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${opts.apiKey}`,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      // 不把上游响应体放进异常：诊断只需要状态码，正文外流是泄露面。
      throw new ProviderHttpError("LLM", response.status);
    }

    let thinkingCharCount = 0;
    let thinkingStopped = false;
    let contentStarted = false;
    const processLine = (raw: string): Array<{ kind: "thinking" | "content"; delta: string }> => {
      const out: Array<{ kind: "thinking" | "content"; delta: string }> = [];
      const line = raw.trim();
      if (!line.startsWith("data:")) return out;
      const data = line.slice(5).trim();
      if (data === "[DONE]") return out;
      let chunk: ChatChunk;
      try {
        chunk = JSON.parse(data) as ChatChunk;
      } catch {
        return out;
      }
      const choices = chunk.choices ?? [];
      if (choices.length === 0) return out;
      const choice = choices[0]!;
      if (choice.finish_reason === "length") meta.truncated = true;
      const delta = choice.delta ?? {};

      if (delta.reasoning_content && !thinkingStopped) {
        if (meta.thinkingTtftMs === 0) {
          meta.thinkingTtftMs = Math.round(performance.now() - started);
          if (meta.ttftMs === 0) meta.ttftMs = meta.thinkingTtftMs;
        }
        thinkingCharCount += delta.reasoning_content.length;
        meta.actualThinkingChars = thinkingCharCount;
        if (opts.thinkingCharLimit && thinkingCharCount >= opts.thinkingCharLimit) {
          if (!thinkingStopped) {
            thinkingStopped = true;
            out.push({ kind: "thinking", delta: "\n[思考已达字符限制，继续生成回答...]" });
          }
          return out;
        }
        out.push({ kind: "thinking", delta: delta.reasoning_content });
      }

      if (delta.content) {
        if (!contentStarted) {
          contentStarted = true;
          meta.contentTtftMs = Math.round(performance.now() - started);
          if (meta.ttftMs === 0) meta.ttftMs = meta.contentTtftMs;
        }
        out.push({ kind: "content", delta: delta.content });
      }
      return out;
    };

    for await (const data of streamDataLines(response.body)) {
      if (data === "[DONE]") break;
      for (const delta of processLine(`data: ${data}`)) yield delta;
    }
  } finally {
    clearTimeout(timeout);
    if (meta.actualThinkingChars > 0) {
      meta.thinkingTokens = estimateTokensChinese(String(meta.actualThinkingChars));
    }
  }
}

async function* streamOpenAICompatible(
  cfg: AppConfig,
  system: string,
  user: string,
  meta: CoachMeta,
): AsyncGenerator<StreamDelta> {
  yield* streamChatCompletions(cfg, system, user, meta, {
    baseUrl: cfg.llmBaseUrl,
    apiKey: cfg.llmApiKey,
    model: cfg.llmModel,
  });
}

async function* streamDeepseek(
  cfg: AppConfig,
  system: string,
  user: string,
  meta: CoachMeta,
): AsyncGenerator<StreamDelta> {
  let extraPayload: Record<string, unknown>;
  let thinkingCharLimit: number | null;
  if (meta.suppressThinking) {
    extraPayload = { thinking: { type: "disabled" } };
    thinkingCharLimit = null;
  } else {
    extraPayload = { thinking: { type: cfg.deepseekThinking } };
    if (cfg.deepseekThinking === "enabled") {
      extraPayload.reasoning_effort = cfg.deepseekReasoningEffort;
      const effortLimits: Record<string, number> = { low: 3000, medium: 6000, high: 9000 };
      thinkingCharLimit = effortLimits[cfg.deepseekReasoningEffort] ?? 6000;
    } else {
      thinkingCharLimit = null;
    }
  }
  yield* streamChatCompletions(cfg, system, user, meta, {
    baseUrl: cfg.deepseekBaseUrl,
    apiKey: cfg.deepseekApiKey,
    model: cfg.deepseekModel,
    extraPayload,
    thinkingCharLimit,
  });
}

async function* streamAnthropic(
  cfg: AppConfig,
  system: string,
  user: string,
  meta: CoachMeta,
): AsyncGenerator<StreamDelta> {
  const started = performance.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), cfg.llmTimeoutMs);
  try {
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": cfg.anthropicApiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: cfg.anthropicModel,
        max_tokens: cfg.llmMaxTokens,
        system,
        messages: [{ role: "user", content: user }],
        stream: true,
      }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      throw new ProviderHttpError("Anthropic", response.status);
    }
    for await (const data of streamDataLines(response.body)) {
      const evt: { type?: string; delta?: { text?: string; stop_reason?: string } } = JSON.parse(data);
      if (evt.type === "content_block_delta" && evt.delta?.text) {
        if (meta.ttftMs === 0) meta.ttftMs = Math.round(performance.now() - started);
        yield { kind: "content", delta: evt.delta.text };
      } else if (evt.type === "message_delta" && evt.delta?.stop_reason === "max_tokens") {
        meta.truncated = true;
      } else if (evt.type === "error") {
        throw new Error("Anthropic stream error");
      } else if (evt.type === "message_stop") {
        break;
      }
    }
  } finally {
    clearTimeout(timeout);
  }
}

const DEPTH_LABEL: Record<string, string> = {
  L0: "只建立感觉",
  L1: "直觉优先",
  L2: "直觉加少量机制",
  L3: "机制加数值",
  L4: "公式推导",
  L5: "研究级细节",
};

function templateText(task: ResolvedTask, appliedLabels: string[]): string {
  const concept = task.concept || "这个概念";
  const depth = task.desiredDepth !== "auto" ? task.desiredDepth : "L2";
  let preferenceNote = appliedLabels.length
    ? `\n\n已按你的稳定偏好调整：${appliedLabels.join("；")}。`
    : "";
  if (task.outputPreference.length > 0) {
    preferenceNote += `\n本轮要求：${task.outputPreference.join("；")}。`;
  }
  let body: string;
  if (depth === "L0") {
    body = "先只建立一个感觉：它把一个原本难以直接处理的问题，转换成更容易观察的一步。先记住输入、变化和结果，不展开公式。";
  } else if (depth === "L1") {
    body = "先用一个最小情境建立直觉，再用一句话说明它为什么有效；暂时不展开完整公式和代码。";
  } else if (depth === "L4") {
    body = "先写清定义和变量，再说明公式每一项的含义，最后把推导连接回直觉。需要时使用 LaTeX 公式。";
  } else if (depth === "L5") {
    body = "先给出核心机制，再讨论假设、边界、复杂度和与相邻方法的差异；公式只在能支撑结论时使用。";
  } else {
    body = "先说它解决的问题，再连接已知前置；随后用最小直觉、机制步骤和边界把它落地。";
  }
  return (
    `下面按「${concept} · ${task.taskScope}」来讲，局部深度 ${depth}（${DEPTH_LABEL[depth] ?? ""}）。\n\n` +
    `${body}\n\n` +
    "当前由内置模板回答：未配置外部模型。配置 RECOACH_LLM_PROVIDER 后，这里将替换为真实讲解，结构与个性化行为保持不变。" +
    `${preferenceNote}`
  );
}

export interface VerifyOutput {
  status: "passed" | "partial" | "unknown";
  checks: string[];
  violations: string[];
}

export function verifyOutput(task: ResolvedTask, text: string): VerifyOutput {
  const checks: string[] = [];
  const violations: string[] = [];
  const hasCode = text.includes("```");
  const hasFormula = /\$[^$]+\$|\\frac|\\sum|\\partial/.test(text);
  if (task.desiredDepth === "L0" || task.desiredDepth === "L1") {
    checks.push("低深度不主动展开公式和代码");
    if (hasFormula) violations.push("low_depth_formula");
    if (hasCode) violations.push("low_depth_code");
  }
  for (const pref of task.outputPreference) {
    if (pref.includes("避免公式")) {
      checks.push("遵守本轮避免公式");
      if (hasFormula) violations.push("formula_forbidden");
    }
    if (pref.includes("不要代码")) {
      checks.push("遵守本轮不要代码");
      if (hasCode) violations.push("code_forbidden");
    }
  }
  if (checks.length === 0) return { status: "unknown", checks, violations };
  return { status: violations.length ? "partial" : "passed", checks, violations };
}

export function splitChunks(text: string): string[] {
  const m = text.match(/[^。！？\n]+[。！？]?|\n+/g);
  return m && m.length > 0 ? m : [text];
}

export async function* streamExplanation(
  cfg: AppConfig,
  args: { system: string; user: string; task: ResolvedTask; appliedLabels: string[]; meta: CoachMeta },
): AsyncGenerator<StreamDelta> {
  const { system, user, task, appliedLabels, meta } = args;
  const [provider, model] = resolveProvider(cfg);
  meta.provider = provider;
  meta.model = model;
  // 记下"本轮原本想要谁"：降级后 provider/model 会被改写成 template，
  // 没有这两个字段就无法回溯本该用哪个模型。
  meta.requestedProvider = provider;
  meta.requestedModel = model;

  if (provider === "template") {
    meta.ttftMs = 1;
    for (const chunk of splitChunks(templateText(task, appliedLabels))) {
      yield { kind: "content", delta: chunk };
    }
    return;
  }

  let streamer:
    | ((s: string, u: string, m: CoachMeta) => AsyncGenerator<StreamDelta>)
    | undefined;
  if (provider === "openai_compatible") streamer = (s, u, m) => streamOpenAICompatible(cfg, s, u, m);
  else if (provider === "deepseek") streamer = (s, u, m) => streamDeepseek(cfg, s, u, m);
  else streamer = (s, u, m) => streamAnthropic(cfg, s, u, m);

  const maxContinuations = Math.max(0, Math.min(2, cfg.llmMaxContinuations));
  let producedAny = false;
  let accumulated = "";
  let prompt = user;
  try {
    while (true) {
      meta.truncated = false;
      let attemptContent = "";
      for await (const d of streamer!(system, prompt, meta)) {
        producedAny = true;
        if (d.kind === "content") {
          attemptContent += d.delta;
          accumulated += d.delta;
        }
        yield d;
      }
      if (!meta.truncated || meta.continuationCount >= maxContinuations) break;
      meta.continuationCount += 1;
      meta.suppressThinking = !attemptContent;
      if (attemptContent) {
        prompt =
          `${user}\n\n【已生成正文】\n${accumulated.slice(-12000)}\n\n` +
          "请只从正文中断处继续，禁止重复已经生成的内容，直接输出后续正文。";
      } else {
        prompt =
          `${user}\n\n` +
          "上一轮思考过长被截断，正文尚未开始。请不要再思考，直接给出完整回答正文。";
      }
    }
    if (meta.truncated && meta.continuationCount < maxContinuations && !producedAny) return;
  } catch (err) {
    if (producedAny) throw err;
    meta.fallback = true;
    // 只记录类别，异常原文不外流——它可能含请求 URL 或上游响应体。
    meta.fallbackReason = classifyProviderFailure(err);
    meta.provider = "template";
    meta.model = "template";
    meta.ttftMs = 1;
    for (const chunk of splitChunks(templateText(task, appliedLabels))) {
      yield { kind: "content", delta: chunk };
    }
  }
}
