import { createProvider, type Api, type Model, type Provider } from "@earendil-works/pi-ai";

const LEGACY_FLASH_IDS = ["deepseek-v4-flash", "deepseek-v4-flash-vision-exp"];

/** Official IDs/capabilities: https://api-docs.deepseek.com/api/list-models/ */
export function withDeepSeekDiscovery(base: Provider): Provider {
  const oldFlash = base.getModels().find(m => m.id === "deepseek-v4-flash");
  if (!oldFlash) return base;
  const flash: Model<Api> = {
    ...oldFlash, id: "deepseek-flash", name: "DeepSeek V4.1 Flash", input: ["text", "image"],
    contextWindow: 1_048_576, maxTokens: 393_216,
    // Discovery does not report pricing. Re:Coach does not expose these placeholder costs as billing.
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  };
  const aliases = (model: Model<Api>) => LEGACY_FLASH_IDS.map(id => ({
    ...model, id, name: `${model.name}（兼容旧 ID：${id}）`,
  }));
  const seed = [flash, ...base.getModels().filter(m => !LEGACY_FLASH_IDS.includes(m.id)), ...aliases(flash)];
  return createProvider({
    id: base.id, name: base.name, baseUrl: base.baseUrl, auth: base.auth,
    models: seed,
    api: { stream: base.stream.bind(base), streamSimple: base.streamSimple.bind(base) },
    async fetchModels(context) {
      if (context.credential?.type !== "api_key" || !context.credential.key) return seed;
      const response = await fetch("https://api.deepseek.com/models", {
        headers: { Authorization: `Bearer ${context.credential.key}` }, redirect: "error",
        signal: AbortSignal.any([context.signal, AbortSignal.timeout(8_000)]),
      });
      if (!response.ok) throw new Error(`DeepSeek model discovery HTTP ${response.status}`);
      const body: unknown = await response.json();
      if (!body || typeof body !== "object" || !("data" in body) || !Array.isArray(body.data)) {
        throw new Error("Invalid DeepSeek model directory");
      }
      const models: Model<Api>[] = [];
      for (const row of body.data) {
        if (!row || typeof row !== "object" || typeof row.id !== "string"
          || !/^[a-z0-9][a-z0-9._/-]{0,127}$/i.test(row.id)) continue;
        const template = seed.find(m => m.id === row.id) ?? flash;
        const model = { ...template, id: row.id, name: typeof row.name === "string" ? row.name : row.id };
        if (Number.isSafeInteger(row.context_window) && row.context_window > 0) model.contextWindow = row.context_window;
        if (Number.isSafeInteger(row.max_output_tokens) && row.max_output_tokens > 0) model.maxTokens = row.max_output_tokens;
        if (Array.isArray(row.input_modalities)) {
          const input = row.input_modalities.filter((v: unknown): v is "text" | "image" => v === "text" || v === "image");
          if (input.length) model.input = input;
        }
        if (Array.isArray(row.effort?.supported_levels)) {
          const levels: string[] = row.effort.supported_levels;
          model.thinkingLevelMap = Object.fromEntries(["minimal", "low", "medium", "high", "xhigh", "max"]
            .map(level => [level, levels.includes(level) ? level : null]));
        }
        models.push(model);
      }
      if (!models.length) throw new Error("Empty DeepSeek model directory");
      const currentFlash = models.find(m => m.id === "deepseek-flash");
      return currentFlash ? [...models.filter(m => !LEGACY_FLASH_IDS.includes(m.id)), ...aliases(currentFlash)] : models;
    },
  });
}
