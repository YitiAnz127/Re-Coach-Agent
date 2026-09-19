import assert from "node:assert/strict";
import test from "node:test";

import { fallbackNotice } from "../src/services/fallback-notice.ts";
import type { ServiceMeta } from "../src/services/service-meta.ts";
import type { TurnPresentation } from "../src/types.ts";

const meta = (over: Partial<ServiceMeta> = {}): ServiceMeta => ({
  provider: "deepseek",
  model: "deepseek-flash",
  configured: true,
  thinkingEnabled: true,
  reasoningEffort: "low",
  fairAbFork: true,
  keysPresent: { deepseek: true },
  ...over,
});

const presentation = (
  metrics: Record<string, unknown>,
): TurnPresentation =>
  ({
    mode: "explain",
    focus: "x",
    plan: [],
    personalization: [],
    metrics: { timeToFirstTokenMs: 0, memorySearchMs: 0, contextCompileMs: 0, memoryCapsuleTokens: 0, totalInputTokens: 0, ...metrics },
    suggestedActions: [],
  }) as TurnPresentation;

test("正常回答不提示降级", () => {
  const notice = fallbackNotice(
    presentation({ provider: "deepseek", fallback: false }),
    meta(),
  );
  assert.equal(notice, null);
});

test("本轮降级时给出可诊断的说明", () => {
  const notice = fallbackNotice(
    presentation({ provider: "template", fallback: true, fallbackReason: "AUTH" }),
    meta(),
  );
  assert.ok(notice);
  assert.equal(notice.label, "本轮为模板降级");
  assert.match(notice.detail, /密钥无效|访问凭证/);
  assert.match(notice.detail, /模板生成/);
  // 必须点明"不是模型输出"，否则用户仍会误以为在做模型对话
  assert.match(notice.detail, /不是模型输出/);
});

test("未知降级原因也要有兜底文案，不能给出空提示", () => {
  const notice = fallbackNotice(
    presentation({ fallback: true, fallbackReason: "SOMETHING_NEW" }),
    meta(),
  );
  assert.ok(notice);
  assert.ok(notice.detail.length > 0);
});

test("旧后端缺 fallback 字段时不能误报成降级", () => {
  // 字段缺失（undefined）不等于降级——否则每次回答都会挂一个假警告
  const notice = fallbackNotice(presentation({ provider: "deepseek" }), meta());
  assert.equal(notice, null);
});

test("填了密钥但 provider 仍是 template 时给出针对性提示", () => {
  const notice = fallbackNotice(
    presentation({ provider: "template", fallback: false }),
    meta({ provider: "template", configured: false, model: "template" }),
  );
  assert.ok(notice);
  assert.equal(notice.label, "密钥已配置但未启用");
  assert.match(notice.detail, /RECOACH_LLM_PROVIDER/);
});

test("未配置密钥时提示是模板模式而不是配置失误", () => {
  const notice = fallbackNotice(
    presentation({ provider: "template" }),
    meta({ provider: "template", configured: false, keysPresent: {} }),
  );
  assert.ok(notice);
  assert.equal(notice.label, "当前为模板模式");
});

test("没有 presentation 时不提示（首屏/加载中）", () => {
  assert.equal(fallbackNotice(undefined, meta()), null);
});
