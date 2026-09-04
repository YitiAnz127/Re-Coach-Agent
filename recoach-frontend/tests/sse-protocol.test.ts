import assert from "node:assert/strict";
import test from "node:test";

import { SseProtocolDecoder, SseProtocolError } from "../src/services/sse-protocol.ts";

const started = 'event: turn.started\ndata: {"type":"turn.started","turnId":"turn_1","mode":"explain","focus":"反向传播","plan":[]}';
const completed = 'event: turn.completed\ndata: {"type":"turn.completed","turnId":"turn_1","presentation":{"mode":"explain","focus":"反向传播","plan":[],"personalization":[],"metrics":{"timeToFirstTokenMs":1,"memorySearchMs":0,"contextCompileMs":0,"memoryCapsuleTokens":0,"totalInputTokens":1},"suggestedActions":[]}}';

test("flushes a final SSE frame even without a trailing blank line", () => {
  const decoder = new SseProtocolDecoder();
  const events = decoder.push(`${started}\n\n`);
  const finalEvents = decoder.finish(completed);

  assert.equal(events[0]?.type, "turn.started");
  assert.equal(finalEvents[0]?.type, "turn.completed");
});

test("rejects a stream with no terminal event", () => {
  const decoder = new SseProtocolDecoder();
  decoder.push(`${started}\n\n`);

  assert.throws(
    () => decoder.finish(),
    (error: unknown) => error instanceof SseProtocolError && error.code === "MISSING_TERMINAL_EVENT",
  );
});

test("rejects duplicate terminal events", () => {
  const decoder = new SseProtocolDecoder();
  decoder.push(`${started}\n\n${completed}\n\n`);

  assert.throws(
    () => decoder.push(`${completed}\n\n`),
    (error: unknown) => error instanceof SseProtocolError && error.code === "EVENT_AFTER_TERMINAL",
  );
});

test("rejects a completed event without a valid presentation", () => {
  const decoder = new SseProtocolDecoder();
  decoder.push(`${started}\n\n`);

  assert.throws(
    () => decoder.push('data: {"type":"turn.completed","turnId":"turn_1"}\n\n'),
    (error: unknown) => error instanceof SseProtocolError && error.code === "INVALID_SSE_EVENT",
  );
});


test("preserves requestId on structured SSE errors", () => {
  const decoder = new SseProtocolDecoder();
  const error = 'data: {"type":"turn.error","turnId":"turn_1","code":"INTERNAL","message":"讲解服务暂时不可用，请重试。","retryable":true,"requestId":"req_1"}';
  const events = decoder.push(`${error}\n\n`);

  assert.equal(events[0]?.type, "turn.error");
  if (events[0]?.type === "turn.error") {
    assert.equal(events[0].requestId, "req_1");
  }
});
