import assert from "node:assert/strict";

const frontend = process.env.RECOACH_FRONTEND_URL || "http://127.0.0.1:4173";
const api = (process.env.RECOACH_API_BASE_URL || new URL("/api/v1", frontend).href).replace(/\/$/, "");
const user = `smoke_${crypto.randomUUID()}`;
const headers = { "Content-Type": "application/json", "x-user-id": user };
async function request(url, options = {}) {
  return fetch(url, { ...options, signal: AbortSignal.timeout(120_000) });
}
async function post(path, body) {
  return request(`${api}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
}
async function turn(sessionId, content, key = crypto.randomUUID()) {
  const response = await post(`/sessions/${sessionId}/turns`, { message: { content }, clientTurnId: key, locale: "zh-CN" });
  assert.equal(response.status, 200);
  const text = await response.text();
  const events = text.split(/\r?\n\r?\n/).flatMap(frame => {
    const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
    return data ? [JSON.parse(data)] : [];
  });
  assert.equal(events.filter(e => ["turn.completed", "turn.error"].includes(e.type)).length, 1);
  assert.equal(events.at(-1)?.type, "turn.completed");
  return events.at(-1);
}

assert.equal((await request(frontend)).status, 200);
const backend = process.env.RECOACH_BACKEND_URL || "http://127.0.0.1:8000";
const origin = new URL(frontend).origin;
const health = await request(new URL("/health", backend), { headers: { Origin: origin } });
assert.equal(health.status, 200);
assert.equal((await health.json()).status, "ok");
assert.equal(health.headers.get("access-control-allow-origin"), origin);
assert.equal((await request(`${api}/meta`)).status, 200);
const created = await post("/sessions", { locale: "zh-CN" });
assert.equal(created.status, 200);
const session = (await created.json()).data.sessionId;
await turn(session, "以后讲概念先给公式再讲直觉");
const key = crypto.randomUUID();
const content = "我想看反向传播的公式推导，从定义开始";
const first = await turn(session, content, key);
assert.ok(first.presentation.personalization.length > 0);
assert.equal((await turn(session, content, key)).turnId, first.turnId);
assert.equal((await post(`/sessions/${session}/turns`, { message: {content}, clientTurnId: crypto.randomUUID(), memoryMode: "off" })).status, 422);
const forkResponse = await post(`/sessions/${session}/forks`, {});
assert.equal(forkResponse.status, 200);
const forks = (await forkResponse.json()).data.forks;
assert.equal(forks.length, 2);
for (const fork of forks) {
  const result = await turn(fork.sessionId, content);
  if (fork.memoryMode === "off") assert.equal(result.presentation.personalization.length, 0);
}
console.log("Smoke passed: page, API, session, memory, replay, validation, and both forks.");
