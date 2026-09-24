/**
 * Store 写入合批回归测试。
 *
 * 背景：save() 会把整份 store.json 重新序列化并原子替换，而文件随历史单调增长。
 * 一轮 Turn 约触发 10 次变更（事件、消息、Brief、概念状态、记忆），逐次落盘
 * 等于每轮重写十遍全量数据，整体退化成 O(n²)——用得越久每轮越慢。
 * Store.transaction 把块内变更合并成一次落盘。
 */
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { Store } from "../src/store.js";
import { loadConfig } from "../src/config.js";
import { makeEvent } from "../src/core/events.js";

function tempStore(): { store: Store; file: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-batch-"));
  const store = new Store(
    loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "template" }),
  );
  return { store, file: path.join(dir, "store.json") };
}

describe("Store 写入合批", () => {
  it("事务内的多次变更只落盘一次", async () => {
    const { store, file } = tempStore();
    const before = store.fileWriteCount;

    await store.transaction(async () => {
      const session = store.createSession();
      store.saveMessage(session.id, "turn_1", "user", "你好");
      store.saveMessage(session.id, "turn_1", "assistant", "你好，我是知返");
      store.logEvent(makeEvent({ userId: "u", turnId: "turn_1", kind: "turn_started", payload: {} }));
      store.logEvent(makeEvent({ userId: "u", turnId: "turn_1", kind: "response_completed", payload: {} }));
    });

    // 6 次变更 → 1 次落盘
    expect(store.fileWriteCount - before).toBe(1);

    // 而且数据确实完整落盘了，不是被吞掉
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    expect(raw.sessions).toHaveLength(1);
    expect(raw.messages).toHaveLength(2);
    expect(raw.events).toHaveLength(2);
  });

  it("事务抛异常时变更仍然落盘，不会静默丢失", async () => {
    const { store, file } = tempStore();
    const before = store.fileWriteCount;

    await expect(
      store.transaction(async () => {
        store.createSession();
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");

    expect(store.fileWriteCount - before).toBe(1);
    expect(JSON.parse(fs.readFileSync(file, "utf8")).sessions).toHaveLength(1);
  });

  it("事务外的变更立即落盘（持久性不降级）", () => {
    const { store, file } = tempStore();
    const before = store.fileWriteCount;

    store.createSession();

    expect(store.fileWriteCount - before).toBe(1);
    expect(JSON.parse(fs.readFileSync(file, "utf8")).sessions).toHaveLength(1);
  });

  it("嵌套事务只在最外层结束时分母落盘一次", async () => {
    const { store } = tempStore();
    const before = store.fileWriteCount;

    await store.transaction(async () => {
      store.createSession();
      await store.transaction(async () => {
        store.createSession();
      });
      // 内层已结束，但外层未结束 → 仍不应落盘
      expect(store.fileWriteCount - before).toBe(0);
    });

    expect(store.fileWriteCount - before).toBe(1);
  });
});
