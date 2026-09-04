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
