import { describe, it, expect, beforeEach } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loadConfig } from "../src/config.js";
import { Store } from "../src/store.js";
import { runTurn } from "../src/orchestrator.js";
import type { AgentTurnEvent } from "../src/agent.js";
import { forgetMemories, writeMemory } from "../src/core/memory.js";

let tmpDir: string;

function makeConfig() {
  return loadConfig({
    ...process.env,
    RECOACH_DATA_DIR: tmpDir,
    RECOACH_LLM_PROVIDER: "template",
  });
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-test2-"));
});

async function run(session: { id: string }, cfg: ReturnType<typeof makeConfig>, store: Store, text: string) {
  const events: AgentTurnEvent[] = [];
  await runTurn({ cfg, store, session, userText: text, onEvent: (e) => events.push(e) });
  return events;
}

describe("Multi-turn behaviors", () => {
  it("session-only rule persists across turns and appears in next explanation", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    const sess = store.createSession(cfg.locale);
    const session = { id: sess.id, memoryOn: true, isFork: false };

    // 建立澄清链：先触发 clarify，再明确方向
    await run(session, cfg, store, "讲讲梯度下降");
    await run(session, cfg, store, "我想先建立梯度下降的整体直觉，用简单例子先不要公式");

    // 现在给 session_only 反馈
    const ev = await run(session, cfg, store, "这次先不要公式");
    const completed = ev.find((e) => e.type === "turn.completed") as
      | { presentation: { suggestedActions: string[]; mode: string } }
      | undefined;
    expect(completed).toBeDefined();

    // 验证 session rule 已存进 store
    const persisted = store.getSession(session.id)!;
    expect(persisted.brief.sessionRules.length).toBeGreaterThan(0, "session rule should be saved");
  });

  it("forget marks memory forgotten and removes from active", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    writeMemory(store, cfg.user, {
      type: "explanation_preference",
      rule: "讲反向传播时先给公式",
      domain: "deep_learning",
      conceptScope: "反向传播",
      sourceEventId: "evt_forget_source",
    });
    expect(store.queryAllActiveMemories(cfg.user).length).toBe(1);
    const forgotten = forgetMemories(store, cfg.user, "公式", "evt_forget_1");
    expect(forgotten.length).toBe(1);
    expect(store.queryAllActiveMemories(cfg.user).length).toBe(0);
    expect(store.listMemories(cfg.user, { status: "forgotten" }).length).toBe(1);
  });

  it("feedback write_longterm creates memory in a turn", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    const sess = store.createSession(cfg.locale);
    const session = { id: sess.id, memoryOn: true, isFork: false };
    await run(session, cfg, store, "以后讲反向传播的时候先给公式，再讲直觉");
    expect(store.queryAllActiveMemories(cfg.user).length).toBe(1);
  });

  it("fork briefs are isolated from each other", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    const sess = store.createSession(cfg.locale);
    await run({ id: sess.id, memoryOn: true, isFork: false }, cfg, store, "讲讲梯度下降");
    const forks = store.createSessionFork(sess.id);
    expect(forks.length).toBe(2);
    expect(forks[0]!.brief).not.toBe(forks[1]!.brief);
  });
});

describe("Memory application loop", () => {
  it("applies a remembered preference in a later turn's personalization", async () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    const sess = store.createSession(cfg.locale);
    const session = { id: sess.id, memoryOn: true, isFork: false };

    // 先记住偏好（全局交互规则）
    await run(session, cfg, store, "以后讲概念的时候先给公式再给直觉");

    // 问一个具体概念，进入 explain
    const ev = await run(session, cfg, store, "我想看反向传播的公式推导，从定义开始");
    const completed = ev.find((e) => e.type === "turn.completed") as
      | { presentation: { personalization: unknown[] } }
      | undefined;
    expect(completed).toBeDefined();
    const pers = completed!.presentation.personalization;
    expect(pers.length).toBeGreaterThan(0);
  });
});

describe("Memory archival & forget regression", () => {
  it("writing a similar active rule archives the old active one", () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    writeMemory(store, cfg.user, {
      type: "explanation_preference",
      rule: "讲反向传播时先给公式",
      domain: "deep_learning",
      conceptScope: "反向传播",
      sourceEventId: "evt_a1",
    });
    writeMemory(store, cfg.user, {
      type: "explanation_preference",
      rule: "讲反向传播时先给公式再讲直觉",
      domain: "deep_learning",
      conceptScope: "反向传播",
      sourceEventId: "evt_a2",
    });
    const active = store.queryAllActiveMemories(cfg.user);
    expect(active.length).toBe(1);
    expect(active[0]!.id).not.toBe(undefined);
    // 旧的应归档
    const archived = store.listMemories(cfg.user, { status: "archived" });
    expect(archived.length).toBe(1);
  });

  it("forget only forgets active memories, not already-forgotten ones", () => {
    const cfg = makeConfig();
    const store = new Store(cfg);
    writeMemory(store, cfg.user, {
      type: "explanation_preference",
      rule: "讲反向传播时先给公式",
      domain: "deep_learning",
      conceptScope: "反向传播",
      sourceEventId: "evt_f1",
    });
    const first = forgetMemories(store, cfg.user, "公式", "evt_fg1");
    expect(first.length).toBe(1);
    expect(store.queryAllActiveMemories(cfg.user).length).toBe(0);
    // 再次遗忘同一关键词：已 forgotten 的不应重复计入
    const second = forgetMemories(store, cfg.user, "公式", "evt_fg2");
    expect(second.length).toBe(0);
    // 用同一 sourceEventId 重试应幂等命中
    const third = forgetMemories(store, cfg.user, "公式", "evt_fg1");
    expect(third.length).toBe(1);
  });
});
