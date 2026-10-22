import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

// Optional browser tooling: npm install --no-save playwright, or supply its module URL.
const { chromium } = await import(process.env.RECOACH_PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
process.env.VITE_DEMO_MODE = "false";
process.env.VITE_API_BASE_URL = "/api/v1";
const presentation = {
  mode: "explain", focus: "布局回归", plan: [], personalization: [], suggestedActions: [],
  metrics: { timeToFirstTokenMs: 1, memorySearchMs: 0, contextCompileMs: 0, memoryCapsuleTokens: 0, totalInputTokens: 0 },
};
let paused = false;
const timers = new Set();
const api = createHttpServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/v1/meta") {
    res.end(JSON.stringify({ data: { llm: { provider: "template", model: "fixture", configured: true, thinkingEnabled: false, reasoningEffort: null }, capabilities: {} } }));
  } else if (req.url === "/api/v1/sessions") {
    res.end(JSON.stringify({ data: { sessionId: "layout-fixture" } }));
  } else if (req.url?.endsWith("/turns") && req.method === "POST") {
    res.setHeader("Content-Type", "text/event-stream");
    const emit = (event) => res.write(`data: ${JSON.stringify(event)}\n\n`);
    emit({ type: "turn.started", turnId: "layout-turn", mode: "explain", focus: "布局回归", plan: [] });
    let chunk = 0;
    const timer = setInterval(() => {
      if (paused) return;
      if (++chunk <= 100) {
        emit({ type: "assistant.delta", turnId: "layout-turn", delta: `第${chunk}段：这是持续增长的流式讲解，验证输入框保持稳定。$x_i^2$\n\n` });
      } else {
        clearInterval(timer); timers.delete(timer);
        emit({ type: "turn.completed", turnId: "layout-turn", presentation });
        res.end();
      }
    }, 30);
    timers.add(timer);
    res.on("close", () => { clearInterval(timer); timers.delete(timer); });
  } else { res.end(JSON.stringify({ data: { turns: [] } })); }
});
let vite, browser;
try {
  await new Promise(resolve => api.listen(0, "127.0.0.1", resolve));
  vite = await createServer({ root, server: { host: "127.0.0.1", port: 4188, strictPort: true, proxy: { "/api": `http://127.0.0.1:${api.address().port}` } }, logLevel: "error" });
  await vite.listen();
  browser = await chromium.launch({ headless: true });
  for (const viewport of [{ width: 1280, height: 900 }, { width: 900, height: 800 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("http://127.0.0.1:4188");
    await page.locator("#coach-prompt").fill("请持续输出一段讲解");
    await page.getByRole("button", { name: "发送问题", exact: true }).click();
    await page.waitForFunction(() => document.querySelector(".assistant-copy")?.textContent?.includes("第1段"));
    await page.evaluate(() => {
      window.composerSamples = [];
      window.sampleComposer = true;
      const sample = () => {
        if (!window.sampleComposer) return;
        const rect = document.querySelector(".composer").getBoundingClientRect();
        window.composerSamples.push({ top: rect.top, bottom: rect.bottom });
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await page.waitForFunction(() => document.querySelector(".assistant-copy")?.textContent?.includes("第30段"));
    paused = true;
    const following = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight - scrollY);
    await page.mouse.move(viewport.width / 2, 250);
    await page.mouse.wheel(0, -700);
    await page.waitForFunction(() => document.documentElement.scrollHeight - innerHeight - scrollY > 300);
    const readingY = await page.evaluate(() => scrollY);
    paused = false;
    await page.waitForFunction(() => document.querySelector(".assistant-copy")?.textContent?.includes("第55段"));
    paused = true;
    const afterReadingY = await page.evaluate(() => scrollY);
    await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" }));
    await page.waitForFunction(() => document.documentElement.scrollHeight - innerHeight - scrollY < 2);
    paused = false;
    await page.waitForFunction(() => !document.querySelector("#coach-prompt").disabled);
    const result = await page.evaluate(() => {
      window.sampleComposer = false;
      const tops = window.composerSamples.map(p => p.top);
      const bottoms = window.composerSamples.map(p => p.bottom);
      const last = document.querySelector(".assistant-copy > :last-child").getBoundingClientRect();
      const box = document.querySelector(".composer").getBoundingClientRect();
      return { movement: Math.max(...tops) - Math.min(...tops), bottomMovement: Math.max(...bottoms) - Math.min(...bottoms), remaining: document.documentElement.scrollHeight - innerHeight - scrollY, lastBottom: last.bottom, composerTop: box.top, composerBottom: box.bottom, pageWidth: document.documentElement.scrollWidth };
    });
    console.log(JSON.stringify({ viewport, following, readingY, afterReadingY, ...result }));
    assert.ok(result.movement <= 1, "流式更新或滚动使输入框跳动");
    assert.ok(Math.abs(afterReadingY - readingY) <= 2, "用户上翻后不应被流式输出拉回底部");
    assert.ok(following < 4 && result.remaining < 4, "在底部时应跟随输出");
    assert.ok(result.lastBottom <= result.composerTop, "最后一段不应被输入框遮挡");
    assert.ok(result.composerBottom <= viewport.height && result.pageWidth <= viewport.width, "输入框应留在视口内");
    const draft = "第一行草稿\n第二行草稿\n第三行草稿\n第四行草稿\n第五行草稿";
    await page.locator("#coach-prompt").fill(draft);
    await page.waitForFunction(() => document.querySelector("#coach-prompt").offsetHeight > 100);
    const expanded = await page.locator(".composer").boundingBox();
    assert.ok(Math.abs(expanded.y + expanded.height - result.composerBottom) <= 1, "多行输入应向上扩展，底边保持固定");
    if (process.env.RECOACH_SCREENSHOT_DIR) {
      await mkdir(process.env.RECOACH_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.RECOACH_SCREENSHOT_DIR, `composer-stream-${viewport.width}.png`) });
    }
    await page.getByRole("button", { name: "Performance", exact: true }).click();
    assert.equal(await page.locator(".composer").count(), 0);
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    assert.equal(await page.locator("#coach-prompt").inputValue(), draft);
    await page.getByRole("button", { name: "New", exact: true }).click();
    assert.equal(await page.locator("#coach-prompt").inputValue(), "");
    const empty = await page.locator(".composer").boundingBox();
    assert.ok(empty.y >= 0 && empty.y + empty.height <= viewport.height, "新会话的输入框应可见");
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Composer streaming layout passed on desktop, tablet and mobile.");
} finally {
  for (const timer of timers) clearInterval(timer);
  await browser?.close(); await vite?.close();
  await new Promise(resolve => api.close(resolve));
}
