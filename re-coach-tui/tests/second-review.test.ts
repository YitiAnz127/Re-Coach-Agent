import { afterEach, expect, it, vi } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { Store } from "../src/store.js";
import { loadConfig } from "../src/config.js";
import { runTurn } from "../src/orchestrator.js";
import type { AgentTurnEvent } from "../src/agent.js";

afterEach(() => vi.unstubAllGlobals());
it("reports actual provider and fallback reason to the TUI", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "review-second-"));
  try {
    const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "openai_compatible", RECOACH_LLM_API_KEY: "test", RECOACH_LLM_BASE_URL: "https://example.test/v1", RECOACH_LLM_MODEL: "test-model" });
    const store = new Store(cfg);
    const session = { id: store.createSession().id, memoryOn: true, isFork: false };
    vi.stubGlobal("fetch", vi.fn(async () => new Response("secret upstream error", { status: 401 })));
    const events: AgentTurnEvent[] = [];
    await runTurn({ cfg, store, session, userText: "我想看反向传播的公式推导，从定义开始", onEvent: e => events.push(e) });
    const completed = events.find(e => e.type === "turn.completed");
    expect(completed?.type).toBe("turn.completed");
    if (completed?.type !== "turn.completed") return;
    expect(completed.presentation.metrics).toMatchObject({ provider: "template", fallback: true, fallbackReason: "AUTH", requestedModel: "test-model" });
    expect(JSON.stringify(events)).not.toContain("secret upstream error");
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

it("uses seconds for the shared timeout setting and rejects unsafe numeric values", () => {
  const cfg = loadConfig({
    RECOACH_LLM_TIMEOUT: "90",
    RECOACH_LLM_MAX_TOKENS: "not-a-number",
    RECOACH_LLM_MAX_CONTINUATIONS: "NaN",
    RECOACH_MEMORY_MAX_SELECTED: "-1",
    RECOACH_MEMORY_HARD_LIMIT: "Infinity",
    RECOACH_MEMORY_CAPSULE_TOKENS: "0",
  });
  expect(cfg.llmTimeoutMs).toBe(90_000);
  expect(cfg.llmMaxTokens).toBe(10_000);
  expect(cfg.llmMaxContinuations).toBe(2);
  expect(cfg.memoryMaxSelected).toBe(3);
  expect(cfg.memoryHardLimit).toBe(4);
  expect(cfg.memoryCapsuleTokens).toBe(280);
});
