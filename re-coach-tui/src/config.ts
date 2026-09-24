// 配置：与后端 config.py 对齐（RECOACH_* 环境变量）
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

export interface AppConfig {
  // 基础
  dataDir: string;
  storeFile: string;
  user: string;
  // 主 Coach 模型
  llmProvider: "template" | "openai_compatible" | "deepseek" | "anthropic";
  llmBaseUrl: string;
  llmApiKey: string;
  llmModel: string;
  deepseekApiKey: string;
  deepseekBaseUrl: string;
  deepseekModel: string;
  deepseekThinking: "enabled" | "disabled";
  deepseekReasoningEffort: "low" | "medium" | "high";
  anthropicApiKey: string;
  anthropicModel: string;
  // LLM 通用
  llmMaxTokens: number;
  llmTimeoutMs: number;
  llmMaxContinuations: number;
  /** 真实模型在首字之前失败时：false=降级为模板，true=直接报 MODEL_UNAVAILABLE。 */
  llmFailFast: boolean;
  // 记忆与预算
  memoryOn: boolean;
  memoryMaxSelected: number;
  memoryHardLimit: number;
  memoryCapsuleTokens: number;
}

export function loadConfig(overrides: Record<string, string | undefined> = {}): AppConfig {
  const resolve = (name: string, fallback = ""): string => overrides[name] ?? process.env[name] ?? fallback;

  const dataDir = resolve("RECOACH_DATA_DIR") || path.join(os.homedir(), ".recoach");

  return {
    dataDir,
    storeFile: path.join(dataDir, "store.json"),
    user: resolve("RECOACH_DEV_USER", "dev_user"),
    llmProvider: (resolve("RECOACH_LLM_PROVIDER", "template") as AppConfig["llmProvider"]),
    llmBaseUrl: resolve("RECOACH_LLM_BASE_URL"),
    llmApiKey: resolve("RECOACH_LLM_API_KEY"),
    llmModel: resolve("RECOACH_LLM_MODEL"),
    deepseekApiKey: resolve("RECOACH_DEEPSEEK_API_KEY") || resolve("DEEPSEEK_API_KEY"),
    deepseekBaseUrl: resolve("RECOACH_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    // 默认值与后端 config.py（也是 .env.example 的权威值）保持一致：
    // 不一致会让"只配密钥不配模型"的用户在两个界面静默跑在不同模型上。
    deepseekModel: resolve("RECOACH_DEEPSEEK_MODEL", "deepseek-v4-flash"),
    deepseekThinking: resolve("RECOACH_DEEPSEEK_THINKING", "enabled") === "disabled" ? "disabled" : "enabled",
    deepseekReasoningEffort: parseReasoningEffort(resolve("RECOACH_DEEPSEEK_REASONING_EFFORT", "medium")),
    anthropicApiKey: resolve("RECOACH_ANTHROPIC_API_KEY") || resolve("ANTHROPIC_API_KEY"),
    anthropicModel: resolve("RECOACH_ANTHROPIC_MODEL", "claude-opus-5"),
    llmMaxTokens: parseInteger(resolve("RECOACH_LLM_MAX_TOKENS", "10000"), 10_000, 1, 1_000_000),
    // 与服务端配置保持一致：RECOACH_LLM_TIMEOUT 的公开单位是秒。
    llmTimeoutMs: parseInteger(resolve("RECOACH_LLM_TIMEOUT", "90"), 90, 1, 3_600) * 1_000,
    llmMaxContinuations: parseInteger(resolve("RECOACH_LLM_MAX_CONTINUATIONS", "2"), 2, 0, 2),
    // 与后端 RECOACH_LLM_FAIL_FAST 同语义，默认同为 false（降级为模板并如实披露）。
    llmFailFast: (() => {
      const v = overrides.RECOACH_LLM_FAIL_FAST ?? process.env.RECOACH_LLM_FAIL_FAST;
      if (v === undefined || v === "") return false;
      return v.toLowerCase() === "true" || v === "1";
    })(),
    memoryOn: (() => {
      const v = overrides.RECOACH_MEMORY_ON ?? process.env.RECOACH_MEMORY_ON;
      if (v === undefined || v === "") return true;
      return v.toLowerCase() === "true" || v === "1";
    })(),
    memoryMaxSelected: parseInteger(resolve("RECOACH_MEMORY_MAX_SELECTED", "3"), 3, 1, 100),
    memoryHardLimit: parseInteger(resolve("RECOACH_MEMORY_HARD_LIMIT", "4"), 4, 1, 100),
    memoryCapsuleTokens: parseInteger(resolve("RECOACH_MEMORY_CAPSULE_TOKENS", "280"), 280, 1, 100_000),
  };
}

function parseInteger(raw: string, fallback: number, min: number, max: number): number {
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < min || value > max) return fallback;
  return value;
}

function parseReasoningEffort(raw: string): AppConfig["deepseekReasoningEffort"] {
  return raw === "low" || raw === "high" ? raw : "medium";
}

export function resolveProvider(cfg: AppConfig): [AppConfig["llmProvider"], string] {
  if (cfg.llmProvider === "openai_compatible" && cfg.llmApiKey && cfg.llmBaseUrl && cfg.llmModel) {
    return ["openai_compatible", cfg.llmModel];
  }
  if (cfg.llmProvider === "deepseek" && cfg.deepseekApiKey) {
    return ["deepseek", cfg.deepseekModel];
  }
  if (cfg.llmProvider === "anthropic" && cfg.anthropicApiKey) {
    return ["anthropic", cfg.anthropicModel];
  }
  return ["template", "template"];
}

export function ensureDataDir(cfg: AppConfig): void {
  if (!fs.existsSync(cfg.dataDir)) {
    fs.mkdirSync(cfg.dataDir, { recursive: true, mode: 0o700 });
  }
}

/**
 * 校验携带 API Key 的自定义端点是否使用 https。
 * 允许 http 只对本机地址开放（本地推理服务常跑在 http://localhost）。
 * 返回错误说明，合规时返回 null。
 */
export function checkBaseUrlSecurity(baseUrl: string, hasKey: boolean): string | null {
  if (!baseUrl || !hasKey) return null;
  let parsed: URL;
  try {
    parsed = new URL(baseUrl);
  } catch {
    return `RECOACH_LLM_BASE_URL 不是合法 URL：${baseUrl}`;
  }
  if (parsed.protocol === "https:") return null;
  const host = parsed.hostname.toLowerCase();
  const isLocal =
    host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "[::1]";
  if (parsed.protocol === "http:" && isLocal) return null;
  return (
    `RECOACH_LLM_BASE_URL 使用 ${parsed.protocol}// 会把 API Key 明文发到网络上。` +
    `请改用 https://（仅本机地址允许 http://）。`
  );
}
