import { afterEach, describe, expect, it, vi } from "vitest";
import { fauxProvider, fauxAssistantMessage, fauxText, fauxThinking, type MutableModels } from "@earendil-works/pi-ai";
import { loadConfig } from "../src/config.js";
import { getModelRuntime } from "../src/model-runtime.js";
import { newCoachMeta, streamExplanation } from "../src/core/coach.js";
import type { ResolvedTask } from "../src/types.js";

const task: ResolvedTask = { goal: "解释梯度", domain: "deep_learning", concept: "梯度", proposition: "解释梯度", knownContext: [], desiredDepth: "auto", taskScope: "直觉解释", outputPreference: [], openQuestions: [], assumptions: [] };
afterEach(() => vi.restoreAllMocks());
function setup() {
  const cfg = loadConfig({ RECOACH_LLM_PROVIDER: "pi", RECOACH_LLM_MODEL: "faux/test" });
  const fake = fauxProvider({ provider: "faux", models: [{ id: "test", reasoning: true }] });
  const runtime = getModelRuntime(cfg);
  (runtime.models as MutableModels).setProvider(fake.provider);
  return { cfg, fake };
}
async function drain(cfg: ReturnType<typeof loadConfig>) {
  const meta = newCoachMeta(); const deltas = [];
  for await (const delta of streamExplanation(cfg, { system: "教学系统提示", user: "当前问题", task, appliedLabels: [], meta })) deltas.push(delta);
  return { meta, deltas };
}

describe("pi stream adapter through the existing coach contract", () => {
  it("maps thinking/text events and retains bounded continuation", async () => {
    const { cfg, fake } = setup();
    fake.setResponses([
      fauxAssistantMessage([fauxThinking("简短思考"), fauxText("第一段")], { stopReason: "length" }),
      fauxAssistantMessage("第二段"),
    ]);
    const { meta, deltas } = await drain(cfg);
    expect(deltas.filter(d => d.kind === "content").map(d => d.delta).join("")).toBe("第一段第二段");
    expect(deltas.some(d => d.kind === "thinking")).toBe(true);
    expect(meta).toMatchObject({ provider: "faux", model: "test", continuationCount: 1, truncated: false, fallback: false });
    expect(fake.state.callCount).toBe(2);
  });
  it("discloses truncation when continuation is disabled", async () => {
    const { cfg, fake } = setup(); cfg.llmMaxContinuations = 0;
    fake.setResponses([fauxAssistantMessage("尚未完成", { stopReason: "length" })]);
    expect((await drain(cfg)).meta.truncated).toBe(true);
  });
  it("classifies auth failures without leaking upstream error text", async () => {
    const { cfg, fake } = setup();
    const runtime = getModelRuntime(cfg);
    (runtime.models as MutableModels).setProvider({ ...fake.provider, auth: { apiKey: {
      name: "test", resolve: async () => { throw new Error("secret-must-not-escape"); },
    } } });
    const result = await drain(cfg);
    expect(result.meta).toMatchObject({ fallback: true, fallbackReason: "AUTH", provider: "template" });
    expect(JSON.stringify(result)).not.toContain("secret-must-not-escape");
    cfg.llmFailFast = true;
    await expect(drain(cfg)).rejects.toMatchObject({ reason: "AUTH" });
  });
  it("does not append template text after a partial provider failure", async () => {
    const { cfg, fake } = setup();
    fake.setResponses([fauxAssistantMessage("partial", { stopReason: "error", errorMessage: "bad" })]);
    await expect(drain(cfg)).rejects.toThrow();
  });
  it("allows a long SSE stream while keep-alive bytes continue arriving", async () => {
    await import("@earendil-works/pi-ai/api/openai-completions");
    const cfg = loadConfig({ RECOACH_LLM_PROVIDER: "pi", RECOACH_DEEPSEEK_API_KEY: "test-only" });
    const runtime = getModelRuntime(cfg);
    cfg.llmModel = `deepseek/${runtime.models.getModels("deepseek")[0]!.id}`;
    cfg.llmTimeoutMs = 250;
    const encoder = new TextEncoder();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      let timer: ReturnType<typeof setTimeout>;
      const body = new ReadableStream<Uint8Array>({ start(c) {
        let count = 0;
        const tick = () => {
          if (count++ < 5) { c.enqueue(encoder.encode(": keep-alive\n\n")); timer = setTimeout(tick, 80); }
          else {
            c.enqueue(encoder.encode('data: {"id":"test","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'));
            c.close();
          }
        };
        init?.signal?.addEventListener("abort", () => { clearTimeout(timer); try { c.error(new DOMException("Aborted", "AbortError")); } catch {} }, { once: true });
        tick();
      } });
      return new Response(body, { headers: { "content-type": "text/event-stream" } });
    });
    const result = await drain(cfg);
    expect(result.meta.fallback).toBe(false);
    expect(result.deltas.map(d => d.delta).join("")).toBe("ok");
  });
  it("aborts a stalled pi request with TIMEOUT", async () => {
    const { cfg, fake } = setup(); cfg.llmTimeoutMs = 20; cfg.llmFailFast = true;
    fake.setResponses([async (_context, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    })]);
    await expect(drain(cfg)).rejects.toMatchObject({ reason: "TIMEOUT" });
  });
});
