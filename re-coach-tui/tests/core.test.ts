import { describe, it, expect, beforeEach } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loadConfig } from "../src/config.js";
import { Store } from "../src/store.js";
import { runGate, detectConcept, MAX_CLARIFY_STREAK } from "../src/core/gate.js";
import { classifyFeedback, writeMemory, retrieve } from "../src/core/memory.js";
import { compileContext } from "../src/core/compiler.js";
import { runTurn } from "../src/orchestrator.js";
import type { AgentTurnEvent } from "../src/agent.js";
import { emptyBrief } from "../src/types.js";

let tmpDir: string;

function makeConfig() {
  return loadConfig({
    ...process.env,
    RECOACH_DATA_DIR: tmpDir,
    RECOACH_LLM_PROVIDER: "template",
  });
}

function collectEvents(session: { id: string }, cfg: ReturnType<typeof makeConfig>, store: Store, text: string) {
  return new Promise<AgentTurnEvent[]>((resolve, reject) => {
    const events: AgentTurnEvent[] = [];
    runTurn({
      cfg,
      store,
      session,
      userText: text,
      onEvent: (e) => events.push(e),
    })
      .then(() => resolve(events))
      .catch(reject);
  });
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-test-"));
});

describe("Clarification Gate", () => {
  it("detects a concept", () => {
    const [domain, concept] = detectConcept("我不懂反向传播");
    expect(concept).toBe("反向传播");
    expect(domain).toBe("deep_learning");
  });

  it("asks clarification for a broad question", () => {
    const r = runGate("讲讲梯度下降", { clarifyStreak: 0 });
    expect(r.decision).toBe("NEEDS_CLARIFICATION");
    expect(r.options?.length).toBe(5);
  });

  it("answers with assumption after 2 clarify streaks", () => {
    const r = runGate("讲讲梯度下降", { clarifyStreak: MAX_CLARIFY_STREAK });
    expect(r.decision).toBe("ANSWER_WITH_ASSUMPTION");
    expect(r.assumption).toBeTruthy();
  });

  it("classifies social greeting as social", () => {
    const r = runGate("你好", { clarifyStreak: 0 });
    expect(r.task.taskScope).toBe("寒暄与开场");
  });
});

describe("Feedback classification", () => {
  it("detects long-term preference", () => {
    const a = classifyFeedback("以后讲X的时候先给公式");
    expect(a.kind).toBe("write_longterm");
    expect(a.memoryType).toBe("interaction_rule");
    expect(a.polarity).toBe("positive");
  });
  it("detects forget", () => {
    const a = classifyFeedback("忘记之前关于公式的偏好");
    expect(a.kind).toBe("forget");
    expect(a.keyword).toBe("公式");
  });
  it("detects session-only", () => {
    const a = classifyFeedback("这次先不要公式");
    expect(a.kind).toBe("session_only");
  });
});

describe("Memory write + retrieve", () => {
  it("writes and retrieves a scoped memory", () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    writeMemory(store, cfg.user, {
      type: "explanation_preference",
      rule: "讲反向传播时先给公式",
      domain: "deep_learning",
      conceptScope: "反向传播",
      sourceEventId: "evt_test_1",
    });
    const task = runGate("讲反向传播", { clarifyStreak: 2 }).task;
    const res = retrieve(store, cfg.user, task, {
      memoryMaxSelected: 3,
      memoryHardLimit: 4,
    });
    expect(res.selected.length).toBeGreaterThan(0);
    expect(res.selected[0]!.rule).toContain("反向传播");
  });
});

describe("Context compiler", () => {
  it("includes memory capsule and task", () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    writeMemory(store, cfg.user, {
      type: "explanation_preference",
      rule: "讲反向传播时先给公式",
      domain: "deep_learning",
      conceptScope: "反向传播",
      sourceEventId: "evt_test_2",
    });
    const task = runGate("讲反向传播", { clarifyStreak: 2 }).task;
    const brief = emptyBrief();
    const compiled = compileContext({
      task,
      brief,
      selectedMemories: store.queryAllActiveMemories(cfg.user),
      conceptStates: [],
      recentMessages: [],
    });
    expect(compiled.user).toContain("学习者偏好");
    expect(compiled.user).toContain("反向传播");
    expect(compiled.capsuleTokens).toBeGreaterThan(0);
  });
});

describe("Full turn via orchestrator (template provider)", () => {
  it("produces completed event with content", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    const sess = store.createSession();
    const session = { id: sess.id, memoryOn: cfg.memoryOn, isFork: false };
    const events = await collectEvents(session, cfg, store, "讲讲反向传播的机制，我完全不懂");
    const completed = events.find((e) => e.type === "turn.completed");
    expect(completed).toBeDefined();
    const deltas = events.filter((e) => e.type === "assistant.delta").map((e) => (e as { delta: string }).delta).join("");
    expect(deltas.length).toBeGreaterThan(0);
  });

  it("asks clarification then resolves on follow-up", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    const sess = store.createSession();
    const session = { id: sess.id, memoryOn: cfg.memoryOn, isFork: false };

    const ev1 = await collectEvents(session, cfg, store, "讲讲梯度下降");
    const c1 = ev1.find((e) => e.type === "turn.completed") as
      | { presentation: { mode: string } }
      | undefined;
    expect(c1?.presentation.mode).toBe("clarify");

    const ev2 = await collectEvents(session, cfg, store, "我想看整体直觉，用简单例子先不要公式");
    const c2 = ev2.find((e) => e.type === "turn.completed") as
      | { presentation: { mode: string } }
      | undefined;
    expect(c2?.presentation.mode).toBe("explain");
  });
});
