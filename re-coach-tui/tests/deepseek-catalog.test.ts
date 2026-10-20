import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "../src/config.js";
import { ModelRuntime } from "../src/model-runtime.js";
import { runModelCommand } from "../src/model-commands.js";

const dirs: string[] = [];
async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), "recoach-ds-catalog-")); dirs.push(dir);
  const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_DEEPSEEK_API_KEY: "test-catalog-key", RECOACH_LLM_PROVIDER: "template" });
  return { cfg, runtime: new ModelRuntime(cfg) };
}
const entry = { id: "deepseek-flash", name: "DeepSeek-V4.1-Flash", context_window: 1048576,
  max_output_tokens: 393216, input_modalities: ["text", "image"], effort: { supported_levels: ["low", "high", "max"] } };
afterEach(async () => { vi.restoreAllMocks(); await Promise.all(dirs.splice(0).map(d => rm(d, { recursive: true, force: true }))); });

describe("DeepSeek current model discovery", () => {
  it("includes the official Flash ID offline and clearly marks legacy aliases", async () => {
    const { runtime } = await fixture();
    expect(runtime.models.getModel("deepseek", "deepseek-flash")?.name).toContain("4.1");
    expect(runtime.models.getModel("deepseek", "deepseek-v4-flash")?.name).toContain("兼容");
    await runtime.select("deepseek", "deepseek-flash");
    expect(runtime.selected().id).toBe("deepseek-flash");
  });
  it("refreshes IDs and capabilities from the authenticated official endpoint", async () => {
    const { runtime } = await fixture();
    const request = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ data: [entry] }));
    const result = await runtime.models.refresh({ providers: ["deepseek"] });
    expect(result.errors.size).toBe(0);
    expect(request).toHaveBeenCalledWith("https://api.deepseek.com/models", expect.objectContaining({
      headers: { Authorization: "Bearer test-catalog-key" }, redirect: "error",
    }));
    expect(runtime.models.getModel("deepseek", "deepseek-flash")).toMatchObject({
      contextWindow: 1048576, maxTokens: 393216, input: ["text", "image"], name: "DeepSeek-V4.1-Flash",
    });
  });
  it("refreshes when opening the picker and when listing models", async () => {
    const { runtime } = await fixture();
    const request = vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ data: [entry] }));
    const ui = { prompt: async () => "deepseek-flash", notify: vi.fn() };
    expect(await runModelCommand("/models deepseek", runtime, ui)).toContain("deepseek/deepseek-flash");
    await runModelCommand("/model deepseek", runtime, ui);
    expect(request).toHaveBeenCalledTimes(2);
    expect(runtime.selected().id).toBe("deepseek-flash");
  });
  it("keeps an offline directory and returns a safe warning when refresh fails", async () => {
    const { runtime } = await fixture();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("secret-body-not-to-display", { status: 401 }));
    const ui = { prompt: async () => "", notify: vi.fn() };
    expect(await runModelCommand("/models deepseek", runtime, ui)).toContain("deepseek-flash");
    expect(ui.notify).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining("刷新失败") }));
    expect(JSON.stringify(ui.notify.mock.calls)).not.toContain("secret-body");
  });
  it("restores a newly discovered ID after restart", async () => {
    const { cfg, runtime } = await fixture();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ data: [{ ...entry, id: "deepseek-new-test" }] }));
    await runtime.models.refresh({ providers: ["deepseek"] });
    await runtime.select("deepseek", "deepseek-new-test");
    const restored = new ModelRuntime(cfg);
    expect(await restored.restoreSelection()).toBe(true);
    expect(restored.selected().id).toBe("deepseek-new-test");
  });
});
