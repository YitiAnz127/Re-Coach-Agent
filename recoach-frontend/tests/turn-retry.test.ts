import assert from "node:assert/strict";
import test from "node:test";

import { prepareAssistantRetry } from "../src/services/turn-retry.ts";
import type { ChatMessage } from "../src/types.ts";

test("retry reuses the logical clientTurnId without duplicating the user message", () => {
  const messages: ChatMessage[] = [
    { id: "user_1", role: "user", content: "解释反向传播", state: "complete" },
    {
      id: "assistant_1",
      role: "assistant",
      content: "网络中断",
      state: "error",
      clientTurnId: "client_fixed",
      requestContent: "解释反向传播",
      retryable: true,
    },
  ];

  const retry = prepareAssistantRetry(messages, "assistant_1");

  assert.equal(retry.clientTurnId, "client_fixed");
  assert.equal(retry.content, "解释反向传播");
  assert.equal(retry.messages.length, 2);
  assert.equal(retry.messages.filter((message) => message.role === "user").length, 1);
  assert.deepEqual(retry.messages[1], {
    ...messages[1],
    content: "",
    state: "streaming",
    presentation: undefined,
    errorCode: undefined,
    thinking: undefined,
    requestId: undefined,
    turnId: undefined,
    elapsedMs: undefined,
    completedAt: undefined,
  });
});

test("non-retryable errors cannot be retried", () => {
  const messages: ChatMessage[] = [
    {
      id: "assistant_1",
      role: "assistant",
      content: "请求冲突",
      state: "error",
      clientTurnId: "client_fixed",
      requestContent: "解释反向传播",
      retryable: false,
    },
  ];

  assert.throws(() => prepareAssistantRetry(messages, "assistant_1"), /不可重试/);
});
