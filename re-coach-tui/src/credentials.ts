import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import lockfile from "proper-lockfile";
import type { AuthOperationOptions, Credential, CredentialStore } from "@earendil-works/pi-ai";

/** Atomic local writes; credentials never enter the learning store or event ledger. */
export async function writePrivateJson(file: string, value: unknown): Promise<void> {
  await mkdir(dirname(file), { recursive: true, mode: 0o700 });
  const temp = `${file}.${randomUUID()}.tmp`;
  try {
    await writeFile(temp, JSON.stringify(value, null, 2) + "\n", { mode: 0o600, flag: "wx" });
    await rename(temp, file);
  } finally {
    await unlink(temp).catch((e: NodeJS.ErrnoException) => { if (e.code !== "ENOENT") throw e; });
  }
}

function validCredential(value: unknown): value is Credential {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (v.type === "oauth") return typeof v.access === "string" && typeof v.refresh === "string"
    && typeof v.expires === "number" && Number.isFinite(v.expires);
  if (v.type !== "api_key" || (v.key !== undefined && typeof v.key !== "string")) return false;
  return v.env === undefined || (!!v.env && typeof v.env === "object" && !Array.isArray(v.env)
    && Object.values(v.env).every(x => typeof x === "string"));
}

export class FileCredentials implements CredentialStore {
  constructor(readonly file: string) {}

  private async load(): Promise<Record<string, Credential>> {
    let text: string;
    try { text = await readFile(this.file, "utf8"); }
    catch (e) { if ((e as NodeJS.ErrnoException).code === "ENOENT") return Object.create(null); throw e; }
    try {
      const value: unknown = JSON.parse(text);
      if (!value || typeof value !== "object" || Array.isArray(value)
        || !Object.values(value).every(validCredential)) throw new Error();
      return Object.assign(Object.create(null), value);
    } catch { throw new Error("凭证文件格式错误；请检查 auth.json，未覆盖原文件。"); }
  }

  async read(providerId: string, options?: AuthOperationOptions) {
    options?.signal?.throwIfAborted();
    return (await this.load())[providerId];
  }

  async list(options?: AuthOperationOptions) {
    options?.signal?.throwIfAborted();
    return Object.entries(await this.load()).map(([providerId, c]) => ({ providerId, type: c.type }));
  }

  private async locked<T>(fn: () => Promise<T>, options?: AuthOperationOptions): Promise<T> {
    await mkdir(dirname(this.file), { recursive: true, mode: 0o700 });
    const deadline = Date.now() + 10_000;
    let release: (() => Promise<void>) | undefined;
    while (!release) {
      options?.signal?.throwIfAborted();
      try { release = await lockfile.lock(this.file, { realpath: false, stale: 30_000, retries: 0 }); }
      catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "ELOCKED" || Date.now() >= deadline) throw e;
        await delay(50, undefined, { signal: options?.signal });
      }
    }
    try { options?.signal?.throwIfAborted(); return await fn(); }
    finally { await release(); }
  }

  async modify(providerId: string, fn: (c: Credential | undefined) => Promise<Credential | undefined>, options?: AuthOperationOptions) {
    if (!/^[a-z0-9][a-z0-9._-]{0,127}$/i.test(providerId)) throw new Error("无效的 Provider 标识。");
    return this.locked(async () => {
      const all = await this.load();
      const next = await fn(all[providerId]);
      // A returned OAuth refresh may already have rotated the upstream token.
      // Once mutation began, commit its result even if the caller just cancelled.
      if (next !== undefined) {
        if (!validCredential(next)) throw new Error("无效的凭证格式。");
        all[providerId] = next;
        await writePrivateJson(this.file, all);
      }
      return all[providerId];
    }, options);
  }

  async delete(providerId: string, options?: AuthOperationOptions) {
    await this.locked(async () => {
      const all = await this.load();
      delete all[providerId];
      options?.signal?.throwIfAborted();
      await writePrivateJson(this.file, all);
    }, options);
  }
}
