import { afterEach, describe, expect, it } from "vitest";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FileCredentials } from "../src/credentials.js";

const dirs: string[] = [];
async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), "recoach-auth-"));
  dirs.push(dir);
  return { file: join(dir, "auth.json"), store: new FileCredentials(join(dir, "auth.json")) };
}
afterEach(async () => { await Promise.all(dirs.splice(0).map(d => rm(d, { recursive: true, force: true }))); });

describe("persistent pi credentials", () => {
  it("persists credentials separately and lists only non-secret metadata", async () => {
    const { file, store } = await fixture();
    await store.modify("deepseek", async () => ({ type: "api_key", key: "test-secret" }));
    expect(await new FileCredentials(file).read("deepseek")).toEqual({ type: "api_key", key: "test-secret" });
    expect(await store.list()).toEqual([{ providerId: "deepseek", type: "api_key" }]);
    expect(JSON.stringify(await store.list())).not.toContain("test-secret");
    await store.delete("deepseek");
    expect(await store.read("deepseek")).toBeUndefined();
  });
  it("serializes refreshes across independent store instances without losing providers", async () => {
    const { file, store } = await fixture();
    await store.modify("anthropic", async () => ({ type: "oauth", access: "a", refresh: "r", expires: 0 }));
    await Promise.all(Array.from({ length: 5 }, () => new FileCredentials(file).modify("anthropic", async c => {
      await new Promise(r => setTimeout(r, 5));
      return { ...c!, type: "oauth", access: "a", refresh: "r", expires: Number(c && "expires" in c ? c.expires : 0) + 1 };
    })));
    expect(await store.read("anthropic")).toMatchObject({ expires: 5 });
  });
  it("fails closed on corrupt credentials and never overwrites them", async () => {
    const { file, store } = await fixture();
    await writeFile(file, "not-json");
    await expect(store.modify("deepseek", async () => ({ type: "api_key", key: "new" }))).rejects.toThrow();
    expect(await readFile(file, "utf8")).toBe("not-json");
  });
  it("keeps old credentials when refresh fails and honors cancellation", async () => {
    const { store } = await fixture();
    await store.modify("deepseek", async () => ({ type: "api_key", key: "old" }));
    await expect(store.modify("deepseek", async () => { throw new Error("refresh failed"); })).rejects.toThrow();
    expect(await store.read("deepseek")).toMatchObject({ key: "old" });
    await expect(store.delete("deepseek", { signal: AbortSignal.abort() })).rejects.toThrow();
    expect(await store.read("deepseek")).toMatchObject({ key: "old" });
  });
  it("commits a rotated token returned just as the caller cancels", async () => {
    const { store } = await fixture();
    const controller = new AbortController();
    await store.modify("anthropic", async () => {
      controller.abort();
      return { type: "oauth", access: "new", refresh: "rotated", expires: 123 };
    }, { signal: controller.signal });
    expect(await store.read("anthropic")).toMatchObject({ refresh: "rotated" });
  });
});
