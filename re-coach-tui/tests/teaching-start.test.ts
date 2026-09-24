import { expect, it } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loadConfig } from "../src/config.js";
import { Store } from "../src/store.js";
import { runTurn } from "../src/orchestrator.js";
import { inferTeachingStart } from "../src/core/teaching.js";
import { runGate } from "../src/core/gate.js";
import type { AgentTurnEvent } from "../src/agent.js";
import type { ResolvedTask, TurnPresentation } from "../src/types.js";

const task: ResolvedTask = {
  goal: "", domain: "deep_learning", concept: "反向传播", proposition: "解释反向传播",
  knownContext: [], desiredDepth: "L4", taskScope: "数学推导", outputPreference: [], openQuestions: [], assumptions: [],
};

it("keeps requested depth separate from an explicit teaching start", () => {
  expect(inferTeachingStart(task, "我是新手，请详细推导反向传播", []).level).toBe("novice");
  expect(inferTeachingStart(task, "我熟悉链式法则，直接推导反向传播", []).level).toBe("advanced");
  expect(inferTeachingStart(task, "请详细解释反向传播", []).level).toBe("unknown");
});

it("does not inherit a concept for a greeting", () => {
  const result = runGate("谢谢", { clarifyStreak: 0, knownContext: ["反向传播"] });
  expect(result.task.taskScope).toBe("寒暄与开场");
  expect(result.task.concept).toBe("");
});

it("records local pace feedback and applies it only to the same concept", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-teaching-"));
  const cfg = loadConfig({ ...process.env, RECOACH_DATA_DIR: tmp, RECOACH_LLM_PROVIDER: "template" });
  const store = new Store(cfg);
  const session = store.createSession();
  async function ask(text: string): Promise<TurnPresentation> {
    const events: AgentTurnEvent[] = [];
    await runTurn({ cfg, store, session, userText: text, onEvent: (event) => events.push(event) });
    const completed = events.find((event) => event.type === "turn.completed");
    expect(completed?.type).toBe("turn.completed");
    return (completed as Extract<AgentTurnEvent, { type: "turn.completed" }>).presentation;
  }
  expect((await ask("请详细解释反向传播的链式法则")).teachingStart?.level).toBe("unknown");
  await ask("/pace fast");
  expect((await ask("请解释反向传播中梯度怎么传递")).teachingStart?.level).toBe("novice");
  expect((await ask("请解释梯度下降的更新步骤")).teachingStart?.level).toBe("unknown");
  const reopened = new Store(cfg);
  expect(reopened.latestTeachingCalibration(cfg.user, "deep_learning", "反向传播")?.level).toBe("novice");
});
