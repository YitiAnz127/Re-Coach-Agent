import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "../src/config.js";
import { ModelRuntime } from "../src/model-runtime.js";
import { runModelCommand } from "../src/model-commands.js";
import { fauxProvider, type MutableModels } from "@earendil-works/pi-ai";

const dirs: string[] = [];
async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), "recoach-models-")); dirs.push(dir);
  const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "template" });
  return { cfg, runtime: new ModelRuntime(cfg), dir };
}
afterEach(async () => { vi.restoreAllMocks(); await Promise.all(dirs.splice(0).map(d => rm(d, { recursive: true, force: true }))); });

describe("pi model selection and commands", () => {
  it("persists an explicit model and restores it without touching learning history", async () => {
    const { runtime, cfg, dir } = await fixture();
    const model = runtime.models.getModels("deepseek")[0]!;
    await runtime.select("deepseek", model.id);
    expect(cfg.llmProvider).toBe("pi");
    const { cfg: next } = await fixture(); next.dataDir = dir;
    const restored = new ModelRuntime(next);
    expect(await restored.restoreSelection()).toBe(true);
    expect(restored.selected().id).toBe(model.id);
    expect(JSON.parse(await readFile(join(dir, "model.json"), "utf8"))).toEqual({ provider: "deepseek", model: model.id });
  });
  it("rejects unknown models without changing current config", async () => {
    const { runtime, cfg } = await fixture();
    await expect(runtime.select("deepseek", "missing")).rejects.toThrow();
    expect(cfg.llmProvider).toBe("template");
  });
  it("routes commands locally, supports API-key login/logout and never returns a secret", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ data: [{ id: "deepseek-flash", name: "DeepSeek V4.1 Flash" }] }));
    const { runtime } = await fixture();
    const model = runtime.models.getModels("deepseek")[0]!;
    const interaction = { prompt: async () => "test-only-secret", notify: () => {} };
    const message = await runModelCommand("/login deepseek api_key", runtime, interaction);
    expect(message).toContain("成功"); expect(message).not.toContain("test-only-secret");
    expect(await runtime.credentials.list()).toEqual([{ providerId: "deepseek", type: "api_key" }]);
    expect(await runModelCommand(`/model deepseek/${model.id}`, runtime, interaction)).toContain(model.id);
    expect(await runModelCommand("/auth", runtime, interaction)).not.toContain("test-only-secret");
    await runModelCommand("/logout deepseek", runtime, interaction);
    expect(await runtime.credentials.list()).toEqual([]);
    expect(await runModelCommand("解释反向传播", runtime, interaction)).toBeUndefined();
    expect(await runModelCommand("/pace fast", runtime, interaction)).toBeUndefined();
  });
  it("rejects unsupported OAuth without asking for a key", async () => {
    const { runtime } = await fixture();
    await expect(runModelCommand("/login deepseek oauth", runtime, {
      prompt: async () => { throw new Error("must not ask"); }, notify: () => {},
    })).rejects.toThrow("不支持");
  });
  it("persists OAuth and serializes refresh on actual pi Models requests", async () => {
    const { runtime } = await fixture();
    const fake = fauxProvider({ provider: "test-oauth" });
    let refreshes = 0;
    (runtime.models as MutableModels).setProvider({ ...fake.provider, auth: { oauth: {
      name: "Test OAuth",
      login: async () => ({ type: "oauth", access: "expired", refresh: "rotate", expires: 0 }),
      refresh: async () => {
        refreshes++; await new Promise(r => setTimeout(r, 10));
        return { type: "oauth", access: "fresh", refresh: "new", expires: Date.now() + 3_600_000 };
      },
      toAuth: async c => ({ apiKey: c.access }),
    } } });
    await runtime.login("test-oauth", "oauth", { prompt: async () => "", notify: () => {} });
    const auth = await Promise.all([runtime.models.getAuth("test-oauth"), runtime.models.getAuth("test-oauth")]);
    expect(refreshes).toBe(1);
    expect(auth.every(a => a?.auth.apiKey === "fresh")).toBe(true);
    expect(await runtime.credentials.read("test-oauth")).toMatchObject({ refresh: "new" });
  });
});
