import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import type { AuthInteraction, AuthType, Models, MutableModels } from "@earendil-works/pi-ai";
import type { AppConfig } from "./config.js";
import { FileCredentials, writePrivateJson } from "./credentials.js";
import { withDeepSeekDiscovery } from "./deepseek-catalog.js";

export class ModelSetupError extends Error {}
export function modelCommandError(error: unknown): string {
  if (error instanceof Error && error.name === "AbortError") return "操作已取消。";
  if (error instanceof ModelSetupError) return error.message;
  return "模型配置操作失败，请检查网络、授权状态和本地文件权限后重试。";
}

/** pi-ai 0.85.1 Models API; no coding-agent loop or tools replace Re:Coach's pipeline. */
export class ModelRuntime {
  readonly credentials: FileCredentials;
  readonly models: Models;
  constructor(readonly cfg: AppConfig, models?: Models) {
    this.credentials = new FileCredentials(join(cfg.dataDir, "auth.json"));
    this.models = models ?? builtinModels({
      credentials: this.credentials,
      authContext: {
        env: async name => {
          if (name === "DEEPSEEK_API_KEY" && cfg.deepseekApiKey) return cfg.deepseekApiKey;
          if (name === "ANTHROPIC_API_KEY" && cfg.anthropicApiKey) return cfg.anthropicApiKey;
          return process.env[name];
        },
        fileExists: async file => existsSync(file.startsWith("~/") ? join(homedir(), file.slice(2)) : file),
      },
    });
    if (!models) {
      const deepseek = this.models.getProvider("deepseek");
      if (deepseek) (this.models as MutableModels).setProvider(withDeepSeekDiscovery(deepseek));
    }
  }

  async refreshCatalog(provider: string, signal?: AbortSignal): Promise<string | undefined> {
    if (provider !== "deepseek") return;
    const result = await this.models.refresh({ providers: [provider], signal });
    if (result.aborted) throw new DOMException("Cancelled", "AbortError");
    if (result.errors.size) return "DeepSeek 模型目录刷新失败，保留离线目录；请检查网络或 API key。";
  }

  selected() {
    const separator = this.cfg.llmModel.indexOf("/");
    const provider = this.cfg.llmModel.slice(0, separator);
    const id = this.cfg.llmModel.slice(separator + 1);
    const model = separator > 0 ? this.models.getModel(provider, id) : undefined;
    if (!model) throw new ModelSetupError("未选择有效模型，请用 /model 选择 provider/model。");
    return model;
  }

  async select(provider: string, id: string) {
    const model = this.models.getModel(provider, id);
    if (!model) throw new ModelSetupError("模型不存在，请用 /model 从目录选择。");
    await writePrivateJson(join(this.cfg.dataDir, "model.json"), { provider, model: id });
    this.cfg.llmProvider = "pi";
    this.cfg.llmModel = `${provider}/${id}`;
    return model;
  }

  async restoreSelection(): Promise<boolean> {
    let text: string;
    try { text = await readFile(join(this.cfg.dataDir, "model.json"), "utf8"); }
    catch (e) { if ((e as NodeJS.ErrnoException).code === "ENOENT") return false; throw e; }
    let saved: { provider?: unknown; model?: unknown };
    try { saved = JSON.parse(text); }
    catch { throw new ModelSetupError("model.json 格式错误，请修正或移走该文件。"); }
    if (!saved || typeof saved.provider !== "string" || typeof saved.model !== "string") {
      throw new ModelSetupError("已保存的模型不可用，请用 recoach model 重新选择。");
    }
    if (!this.models.getModel(saved.provider, saved.model)) await this.refreshCatalog(saved.provider);
    if (!this.models.getModel(saved.provider, saved.model)) throw new ModelSetupError("已保存的模型不可用，请用 recoach model 重新选择。");
    this.cfg.llmProvider = "pi";
    this.cfg.llmModel = `${saved.provider}/${saved.model}`;
    return true;
  }

  async login(providerId: string, type: AuthType, interaction: AuthInteraction): Promise<void> {
    const provider = this.models.getProvider(providerId);
    if (!provider || (type === "oauth" ? !provider.auth.oauth : !provider.auth.apiKey?.login)) {
      throw new ModelSetupError("此 Provider 不支持所选登录方式。");
    }
    await this.models.login(providerId, type, interaction);
  }
}

const runtimes = new WeakMap<AppConfig, ModelRuntime>();
export function getModelRuntime(cfg: AppConfig): ModelRuntime {
  let runtime = runtimes.get(cfg);
  if (!runtime) { runtime = new ModelRuntime(cfg); runtimes.set(cfg, runtime); }
  return runtime;
}
