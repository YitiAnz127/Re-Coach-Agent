import { afterEach, describe, expect, it, vi } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loadConfig } from "../src/config.js";
import { Store } from "../src/store.js";
import { writeMemory, forgetMemories } from "../src/core/memory.js";
import { runTurn } from "../src/orchestrator.js";
import { runGate } from "../src/core/gate.js";
import { streamExplanation, newCoachMeta } from "../src/core/coach.js";
import type { AgentTurnEvent } from "../src/agent.js";

const dirs: string[] = [];
function setup() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-review-"));
  dirs.push(dir);
  const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "template" });
  return { cfg, store: new Store(cfg) };
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

describe("persistence and memory regressions", () => {
  it("refuses corrupt storage without replacing user data", () => {
    const { cfg } = setup();
    for (const content of ['{broken', '{}', 'null']) {
      fs.writeFileSync(cfg.storeFile, content);
      expect(() => new Store(cfg)).toThrow();
      expect(fs.readFileSync(cfg.storeFile, "utf8")).toBe(content);
    }
  });
  it("preserves message order when timestamps tie", () => {
    const { cfg, store } = setup();
    const session = store.createSession(cfg.locale);
    vi.spyOn(Date, "now").mockReturnValue(100);
    store.saveMessage(session.id, "t1", "user", "question");
    store.saveMessage(session.id, "t1", "assistant", "answer");
    expect(store.recentMessages(session.id, 1)[0]?.content).toBe("answer");
    expect(store.recentMessages(session.id).map(m => m.content)).toEqual(["question", "answer"]);
  });
  it("isolates copied fork turns and message updates", () => {
    const { cfg, store } = setup();
    const session = store.createSession(cfg.locale);
    store.saveMessage(session.id, "t1", "user", "question");
    const [a, b] = store.createSessionFork(session.id);
    const ma = store.messagesFor(a!.id)[0]!;
    const mb = store.messagesFor(b!.id)[0]!;
    expect(ma.turnId).not.toBe(mb.turnId);
    store.saveMessage(b!.id, mb.turnId, "user", "changed");
    expect(store.messagesFor(a!.id)[0]!.content).toBe("question");
  });
  it("does not reactivate an archived write on replay", () => {
    const { cfg, store } = setup();
    const args = { type: "interaction_rule" as const, rule: "以后先给公式", sourceEventId: "old" };
    const old = writeMemory(store, cfg.user, args);
    const newer = writeMemory(store, cfg.user, { ...args, rule: "以后先给公式再讲直觉", sourceEventId: "new" });
    expect(writeMemory(store, cfg.user, args).id).toBe(old.id);
    expect(store.queryAllActiveMemories(cfg.user).map(m => m.id)).toEqual([newer.id]);
  });
  it("does not apply forgotten global interaction rules", async () => {
    const { cfg, store } = setup();
    writeMemory(store, cfg.user, { type: "interaction_rule", rule: "以后先给公式", sourceEventId: "old" });
    forgetMemories(store, cfg.user, "公式", "forget");
    const session = { id: store.createSession(cfg.locale).id, memoryOn: true, isFork: false };
    const events: AgentTurnEvent[] = [];
    await runTurn({ cfg, store, session, userText: "我想看反向传播的公式推导，从定义开始", onEvent: e => events.push(e) });
    const completed = events.find(e => e.type === "turn.completed");
    expect(completed?.type === "turn.completed" && completed.presentation.personalization).toEqual([]);
  });
});

describe("provider streaming regressions", () => {
  function mockStream(chunks: string[]) {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({ start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    } }))));
  }
  async function collect(provider: "openai_compatible" | "anthropic") {
    const { cfg } = setup();
    Object.assign(cfg, { llmProvider: provider, llmApiKey: "test", llmBaseUrl: "https://example.test/v1", llmModel: "test", anthropicApiKey: "test", llmMaxContinuations: 0 });
    const meta = newCoachMeta();
    const deltas = [];
    for await (const delta of streamExplanation(cfg, { system: "test", user: "test", task: runGate("解释梯度下降", { clarifyStreak: 0 }).task, appliedLabels: [], meta })) deltas.push(delta);
    return { text: deltas.filter(d => d.kind === "content").map(d => d.delta).join(""), meta };
  }
  it("preserves JSON split across transport chunks", async () => {
    mockStream(['data: {"choices":[{"delta":{"con', 'tent":"完整回答"}}]}\n\n', 'data: [DONE]\n\n']);
    expect((await collect("openai_compatible")).text).toBe("完整回答");
  });
  it("recognizes Anthropic truncation in delta.stop_reason", async () => {
    mockStream(['data: {"type":"content_block_delta","delta":{"text":"partial"}}\n\n', 'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}\n\n']);
    expect((await collect("anthropic")).meta.truncated).toBe(true);
  });
  it("flushes an Anthropic final frame without trailing newline", async () => {
    mockStream(['data: {"type":"content_block_delta","delta":{"text":"final"}}']);
    expect((await collect("anthropic")).text).toBe("final");
  });
});

it("keeps fork memory content after live memory changes and reload", async () => {
  const { cfg, store } = setup();
  const memory = writeMemory(store, cfg.user, { type: "interaction_rule", rule: "以后先给公式", sourceEventId: "frozen" });
  const source = store.createSession(cfg.locale);
  const [fork] = store.createSessionFork(source.id);
  forgetMemories(store, cfg.user, "公式", "forget_live");
  const reopened = new Store(cfg);
  const events: AgentTurnEvent[] = [];
  await runTurn({ cfg, store: reopened, session: { id: fork!.id, isFork: true, memoryOn: true }, userText: "我想看反向传播的公式推导，从定义开始", onEvent: e => events.push(e) });
  const completed = events.find(e => e.type === "turn.completed");
  expect(completed?.type === "turn.completed" && completed.presentation.personalization.map(m => m.memoryId)).toContain(memory.id);
});

it("reports persistence failure instead of completing the turn", async () => {
  const { cfg, store } = setup();
  const session = { id: store.createSession(cfg.locale).id, isFork: false, memoryOn: true };
  const original = store.saveMessage.bind(store);
  vi.spyOn(store, "saveMessage").mockImplementation((sid, tid, role, text) => {
    if (role === "assistant") throw new Error("disk full");
    original(sid, tid, role, text);
  });
  const events: AgentTurnEvent[] = [];
  await runTurn({ cfg, store, session, userText: "我想看反向传播的公式推导，从定义开始", onEvent: e => events.push(e) });
  expect(events.at(-1)?.type).toBe("turn.error");
  expect(events.some(e => e.type === "turn.completed")).toBe(false);
});
