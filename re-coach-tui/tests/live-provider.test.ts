/**
 * TUI 真模型端到端（**可选**：未设置 RECOACH_DEEPSEEK_API_KEY 时整体跳过）。
 *
 * 为什么单独放一个：TUI 是后端管线的独立实现——`core/coach.ts` 自己发请求、
 * 自己解 SSE、自己判降级。单元测试全部使用替身，从未连过真端点，而历史上面向
 * "两侧漂移"的问题恰恰都出在这一层。只有真端点能验证这些：
 *   - SSE 分帧（含跨块边界与多字节字符）
 *   - thinking / content 分流与续写策略
 *   - 降级披露与 fail-fast 的实际失败分类
 *
 * 用法（本地手动跑，CI 不做，因为它需要付费 key 与外网）：
 *   RECOACH_DEEPSEEK_API_KEY=sk-xxx npx vitest --run tests/live-provider.test.ts
 */
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { Store } from "../src/store.js";
import { loadConfig } from "../src/config.js";
import { runTurn } from "../src/orchestrator.js";
import type { AgentTurnEvent } from "../src/agent.js";

const apiKey = (process.env.RECOACH_DEEPSEEK_API_KEY ?? "").trim();
const enabled = apiKey.length > 0;

describe.skipIf(!enabled)("TUI 真模型 E2E", () => {
  it(
    "整条管线跑通：gate → 记忆 → 编译 → 真实模型 → 落库",
    async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-live-"));
      const cfg = loadConfig({
        RECOACH_DATA_DIR: dir,
        RECOACH_LLM_PROVIDER: "deepseek",
        // 默认 medium 档首字可到 48s，测试用 low 保证反馈快
        RECOACH_DEEPSEEK_REASONING_EFFORT:
          process.env.RECOACH_DEEPSEEK_REASONING_EFFORT ?? "low",
      });

      const store = new Store(cfg);
      const session = store.createSession();
      const events: AgentTurnEvent[] = [];

      await runTurn({
        cfg,
        store,
        session: { id: session.id, memoryOn: cfg.memoryOn, isFork: false },
        userText: "我不懂 LayerNorm 和 BatchNorm 在训练时的区别，请从机制上讲。",
        onEvent: (e) => events.push(e),
      });

      const failed = events.find((e) => e.type === "turn.error");
      const done = events.find((e) => e.type === "turn.completed");
      const text = events
        .filter((e) => e.type === "assistant.delta")
        .map((e) => (e as { delta: string }).delta)
        .join("");

      expect(failed, `本轮失败：${JSON.stringify(failed)}`).toBeUndefined();
      expect(done).toBeTruthy();
      // 真实模型必须给出实质正文，而不是空串或模板兜底
      expect(text.length).toBeGreaterThan(100);
      expect(done!.presentation.metrics.provider).toBe("deepseek");
      expect(done!.presentation.metrics.fallback).toBe(false);

      // 流式增量与落库正文必须逐字一致（不一致会让刷新后看到的内容变样）
      const turns = store.sessionTurns(session.id);
      expect(turns).toHaveLength(1);
      expect(turns[0]!.assistantText).toBe(text);
    },
    300_000,
  );
});
