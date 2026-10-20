import type { ModelsSimpleStreamOptions } from "@earendil-works/pi-ai";
import type { AppConfig } from "../config.js";
import { getModelRuntime } from "../model-runtime.js";
import type { StreamDelta } from "../types.js";
import type { CoachMeta } from "./coach.js";

export class PiStreamFailure extends Error {
  constructor(readonly reason: string) { super(`模型调用失败（${reason}）。`); }
}

function reasonFor(error: unknown, status: number, timedOut: boolean): string {
  if (timedOut) return "TIMEOUT";
  const code = error && typeof error === "object" && "code" in error ? error.code : undefined;
  if (code === "auth" || code === "oauth" || status === 401 || status === 403) return "AUTH";
  if (status === 429) return "QUOTA";
  if (status >= 500) return "PROVIDER_ERROR";
  if (status >= 400) return "HTTP_ERROR";
  if (error instanceof TypeError) return "NETWORK";
  return "PROVIDER_ERROR";
}

/** Map pi events to Re:Coach deltas; continuation/fallback stay in coach.ts. */
export async function* streamPi(
  cfg: AppConfig, system: string, user: string, meta: CoachMeta,
): AsyncGenerator<StreamDelta> {
  const runtime = getModelRuntime(cfg);
  const model = runtime.selected();
  meta.provider = meta.requestedProvider = model.provider;
  meta.model = meta.requestedModel = model.id;
  const started = performance.now();
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let timedOut = false, status = 0, terminal = false, authResolved = false;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { timedOut = true; controller.abort(); }, cfg.llmTimeoutMs);
  };
  // Observe transport bytes, including keep-alives: stream deltas alone are not a heartbeat.
  const watchedFetch: typeof fetch = async (input, init) => {
    const signal = init?.signal ? AbortSignal.any([controller.signal, init.signal]) : controller.signal;
    const response = await fetch(input, { ...init, signal });
    status = response.status;
    arm();
    if (!response.body) return response;
    const reader = response.body.getReader();
    const body = new ReadableStream<Uint8Array>({
      async pull(c) {
        try {
          const chunk = await reader.read(); arm();
          if (chunk.done) { reader.releaseLock(); c.close(); }
          else c.enqueue(chunk.value);
        } catch (error) { c.error(error); }
      },
      async cancel(reason) { try { await reader.cancel(reason); } finally { reader.releaseLock(); } },
    });
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  };
  arm();
  try {
    const options: ModelsSimpleStreamOptions = {
      signal: controller.signal, fetch: watchedFetch, transport: "sse", maxRetries: 0,
      timeoutMs: cfg.llmTimeoutMs, maxTokens: Math.min(cfg.llmMaxTokens, model.maxTokens),
      toolChoice: "none",
      // Mark completion of auth without resolving it twice (which can rotate OAuth tokens).
      transformHeaders: headers => { authResolved = true; return headers; },
      reasoning: meta.suppressThinking ? undefined : cfg.deepseekReasoningEffort,
      onResponse: response => { status = response.status; arm(); },
    };
    const stream = runtime.models.streamSimple(model, {
      systemPrompt: system,
      messages: [{ role: "user", content: user, timestamp: Date.now() }],
    }, options);
    for await (const event of stream) {
      if (controller.signal.aborted) throw new PiStreamFailure(timedOut ? "TIMEOUT" : "PROVIDER_ERROR");
      arm();
      if (event.type === "text_delta" || event.type === "thinking_delta") {
        const elapsed = Math.max(1, Math.round(performance.now() - started));
        if (!meta.ttftMs) meta.ttftMs = elapsed;
        if (event.type === "thinking_delta") {
          if (!meta.thinkingTtftMs) meta.thinkingTtftMs = elapsed;
          meta.actualThinkingChars += Array.from(event.delta).length;
          yield { kind: "thinking", delta: event.delta };
        } else {
          if (!meta.contentTtftMs) meta.contentTtftMs = elapsed;
          yield { kind: "content", delta: event.delta };
        }
      } else if (event.type === "done") {
        terminal = true;
        meta.truncated = event.reason === "length";
      } else if (event.type === "error") {
        throw new PiStreamFailure(!authResolved && !timedOut ? "AUTH" : reasonFor(event.error, status, timedOut));
      } else if (event.type === "toolcall_start") {
        throw new PiStreamFailure("PROVIDER_ERROR");
      }
    }
    if (!terminal) throw new PiStreamFailure("PROVIDER_ERROR");
  } catch (error) {
    if (error instanceof PiStreamFailure) throw error;
    throw new PiStreamFailure(reasonFor(error, status, timedOut));
  } finally { clearTimeout(timer); controller.abort(); }
}
