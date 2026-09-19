import assert from "node:assert/strict";
import test from "node:test";

import {
  formatServiceProvider,
  parseServiceMeta,
} from "../src/services/service-meta.ts";

const deepseekPayload = {
  data: {
    product: "知返 Re:Coach",
    version: "1.1.0",
    phase: "p1",
    policyVersion: "policy_1.1.0",
    schemaVersion: 3,
    llm: {
      provider: "deepseek",
      model: "deepseek-v4-flash",
      configured: true,
      thinkingEnabled: true,
      reasoningEffort: "high",
      keysPresent: { deepseek: true, anthropic: false, openaiCompatible: false },
    },
    capabilities: {
      fairAbFork: true,
    },
  },
};

test("parses DeepSeek service metadata", () => {
  const meta = parseServiceMeta(deepseekPayload);

  assert.deepEqual(meta, {
    provider: "deepseek",
    model: "deepseek-v4-flash",
    configured: true,
    thinkingEnabled: true,
    reasoningEffort: "high",
    fairAbFork: true,
    keysPresent: { deepseek: true, anthropic: false, openaiCompatible: false },
  });
});

test("keysPresent is optional for older backends", () => {
  // 旧后端不返回该字段时不应导致整体解析失败，按"未知"处理。
  const payload = structuredClone(deepseekPayload);
  delete (payload.data.llm as { keysPresent?: unknown }).keysPresent;

  assert.deepEqual(parseServiceMeta(payload).keysPresent, {});
});

test("keysPresent only keeps boolean true entries", () => {
  // 后端可能返回非布尔值（版本不一致）；只有严格 true 才算"已配置"，
  // 否则会把 "false" 字符串之类的值误判成已配置。
  const payload = structuredClone(deepseekPayload);
  (payload.data.llm as { keysPresent: unknown }).keysPresent = {
    deepseek: true,
    anthropic: "false",
    openaiCompatible: 0,
  };

  assert.deepEqual(parseServiceMeta(payload).keysPresent, {
    deepseek: true,
    anthropic: false,
    openaiCompatible: false,
  });
});

test("treats an older backend without fair fork capability as unsupported", () => {
  const payload = structuredClone(deepseekPayload);
  delete (payload.data.capabilities as { fairAbFork?: boolean }).fairAbFork;

  assert.equal(parseServiceMeta(payload).fairAbFork, false);
});

test("formats DeepSeek as a user-facing provider name", () => {
  assert.equal(formatServiceProvider("deepseek"), "DeepSeek");
  assert.equal(formatServiceProvider("openai_compatible"), "OpenAI-compatible");
  assert.equal(formatServiceProvider("template"), "本地模板");
});

test("rejects invalid service metadata", () => {
  assert.throws(() => parseServiceMeta({ data: { llm: { provider: 1 } } }), /服务信息/);
});
