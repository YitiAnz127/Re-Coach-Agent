/**
 * `RECOACH_LLM_TIMEOUT` 的语义：**两次数据之间的间隔**上限，不是整轮总时长。
 *
 * 回归：TUI 原本用一个固定的 setTimeout 当总时长上限，于是
 * `RECOACH_DEEPSEEK_REASONING_EFFORT=high`（首字本身就可能 >60s）时，
 * 正文会在 90s 处被拦腰截断；而后端把同一个值传给 httpx 的 read timeout，
 * 跑同一轮会正常完成——同一份配置在两个界面上行为不同。
 */
import { afterEach, describe, it, expect, vi } from "vitest";
import { loadConfig } from "../src/config.js";
import { newCoachMeta, streamExplanation } from "../src/core/coach.js";
import type { ResolvedTask } from "../src/types.js";

const SSE_LINE = (text: string) =>
  `data: {"choices":[{"delta":{"content":"${text}"}}]}\n\n`;

/**
 * 构造一个 SSE 响应流。gaps[i] 是第 i 个 chunk 之前的延迟（毫秒）。
 * 会监听 abort：真实 fetch 在被中止时会让读取失败，这里必须模拟同样的行为，
 * 否则"超时是否真的生效"就测不出来。
 */
function sseStream(chunks: string[], gaps: number[], signal?: AbortSignal): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  let settled = false;
  return new ReadableStream({
    start(controller) {
      const fail = () => {
        if (settled) return;
        settled = true;
        controller.error(new DOMException("Aborted", "AbortError"));
      };
      signal?.addEventListener("abort", fail, { once: true });
      const push = () => {
        if (settled) return;
        if (signal?.aborted) return fail();
        if (index >= chunks.length) {
          settled = true;
          controller.close();
          return;
        }
        const delay = gaps[index] ?? 0;
        index += 1;
        setTimeout(() => {
          if (settled) return;
          controller.enqueue(encoder.encode(chunks[index - 1]!));
          push();
        }, delay);
      };
      push();
    },
  });
}

function task(): ResolvedTask {
  return {
    goal: "解释梯度下降",
    domain: "deep_learning",
    concept: "梯度下降",
    proposition: "解释梯度下降",
    knownContext: [],
    desiredDepth: "auto",
    taskScope: "直觉解释",
    outputPreference: [],
    openQuestions: [],
    assumptions: [],
  };
}

/** 只留 1 秒超时，让测试跑得快；其余走默认。 */
function cfgWithTimeoutSeconds(seconds: string) {
  return loadConfig({
    RECOACH_LLM_PROVIDER: "deepseek",
    RECOACH_DEEPSEEK_API_KEY: "k",
    RECOACH_LLM_TIMEOUT: seconds,
  });
}

async function drain(cfg: ReturnType<typeof loadConfig>) {
  const meta = newCoachMeta();
  const parts: string[] = [];
  for await (const delta of streamExplanation(cfg, {
    system: "s",
    user: "u",
    task: task(),
    appliedLabels: [],
    meta,
  })) {
    if (delta.kind === "content") parts.push(delta.delta);
  }
  return { text: parts.join(""), meta };
}

function stubFetch(chunks: string[], gaps: number[]) {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) =>
    new Response(sseStream(chunks, gaps, init.signal as AbortSignal), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    }),
  ));
}

describe("LLM 超时语义（与后端 httpx read timeout 对齐）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("持续有数据时，总耗时远超阈值也不会被截断", async () => {
    // 超时 1s；每 350ms 一个 chunk、共 6 个 → 总耗时约 2.1s（> 2× 阈值）
    const pieces = ["一", "二", "三", "四", "五", "六"];
    stubFetch(
      [...pieces.map(SSE_LINE), "data: [DONE]\n\n"],
      [350, 350, 350, 350, 350, 350, 350],
    );

    const { text, meta } = await drain(cfgWithTimeoutSeconds("1"));

    // 全程无中断 → 六个字都要在，且没有降级
    expect(text).toBe("一二三四五六");
    expect(meta.fallback).toBe(false);
  }, 20_000);

  it("中途长时间无数据仍会按超时中止（超时本身不能失效）", async () => {
    // 第 2 个 chunk 前停 1.6s > 1s 阈值 → 应当被中止。
    // 此时已经吐出过内容，所以按既定策略"如实报错"，而不是静默降级成模板
    // （只有一个字都没出时才降级，见 streamExplanation 的 producedAny 分支）。
    stubFetch(
      [SSE_LINE("一"), SSE_LINE("二"), "data: [DONE]\n\n"],
      [100, 1600, 100],
    );

    await expect(drain(cfgWithTimeoutSeconds("1"))).rejects.toMatchObject({
      name: "AbortError",
    });
  }, 20_000);

  it("连接阶段（首字节之前）同样受超时约束", async () => {
    stubFetch([SSE_LINE("一"), "data: [DONE]\n\n"], [1600, 100]);

    const { meta } = await drain(cfgWithTimeoutSeconds("1"));

    expect(meta.fallbackReason).toBe("TIMEOUT");
  }, 20_000);
});
