export type ServiceProvider =
  | "template"
  | "openai_compatible"
  | "deepseek"
  | "anthropic"
  | "demo";

export interface ServiceMeta {
  provider: ServiceProvider;
  model: string;
  configured: boolean;
  thinkingEnabled: boolean;
  reasoningEffort: "low" | "medium" | "high" | null;
  fairAbFork: boolean;
  teachingCalibration: boolean;
  /** 各 provider 是否已配置密钥（布尔，不含密钥内容）。旧后端可能缺失。 */
  keysPresent: Record<string, boolean>;
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isServiceProvider(value: unknown): value is ServiceProvider {
  return isProvider(value);
}

function isProvider(value: unknown): value is ServiceProvider {
  return (
    value === "template" ||
    value === "openai_compatible" ||
    value === "deepseek" ||
    value === "anthropic" ||
    value === "demo"
  );
}

function isEffort(value: unknown): value is ServiceMeta["reasoningEffort"] {
  return value === null || value === "low" || value === "medium" || value === "high";
}

export function parseServiceMeta(payload: unknown): ServiceMeta {
  if (
    !isRecord(payload) ||
    !isRecord(payload.data) ||
    !isRecord(payload.data.llm) ||
    !isRecord(payload.data.capabilities)
  ) {
    throw new Error("服务信息响应不完整。");
  }
  const llm = payload.data.llm;
  const capabilities = payload.data.capabilities;
  if (
    !isProvider(llm.provider) ||
    typeof llm.model !== "string" ||
    typeof llm.configured !== "boolean" ||
    typeof llm.thinkingEnabled !== "boolean" ||
    !isEffort(llm.reasoningEffort) ||
    (capabilities.fairAbFork !== undefined && typeof capabilities.fairAbFork !== "boolean") ||
    (capabilities.teachingCalibration !== undefined && typeof capabilities.teachingCalibration !== "boolean")
  ) {
    throw new Error("服务信息字段无效。");
  }
  return {
    provider: llm.provider,
    model: llm.model,
    configured: llm.configured,
    thinkingEnabled: llm.thinkingEnabled,
    reasoningEffort: llm.reasoningEffort,
    fairAbFork: capabilities.fairAbFork === true,
    teachingCalibration: capabilities.teachingCalibration === true,
    // 可选字段：旧后端不返回时按"未知"处理（空对象），不影响其余功能。
    keysPresent: isRecord(llm.keysPresent)
      ? Object.fromEntries(
          Object.entries(llm.keysPresent).map(([k, v]) => [k, v === true]),
        )
      : {},
  };
}

export function formatServiceProvider(provider: ServiceProvider) {
  switch (provider) {
    case "deepseek":
      return "DeepSeek";
    case "anthropic":
      return "Anthropic";
    case "openai_compatible":
      return "OpenAI-compatible";
    case "demo":
      return "本地演示";
    default:
      return "本地模板";
  }
}

export const demoServiceMeta: ServiceMeta = {
  provider: "demo",
  model: "local-mock",
  configured: false,
  keysPresent: {},
  thinkingEnabled: false,
  reasoningEffort: null,
  fairAbFork: false,
  teachingCalibration: false,
};
