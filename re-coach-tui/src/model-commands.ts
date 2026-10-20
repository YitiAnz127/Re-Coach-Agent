import type { AuthInteraction, AuthType } from "@earendil-works/pi-ai";
import { ModelSetupError, type ModelRuntime } from "./model-runtime.js";

export const MODEL_HELP = [
  "/login [provider] [oauth|api_key]  登录或保存 API key",
  "/logout [provider]  删除该 Provider 的本地凭证",
  "/model [provider/model]  选择并保存模型",
  "/models [provider]  查看模型目录",
  "/auth  查看本地凭证状态（不显示密钥）",
  "/help  显示帮助；/pace basic|ok|fast 调整教学起点",
].join("\n");

export function isModelCommand(text: string): boolean {
  return text.startsWith("/") && !/^\/pace(?:\s|$)/.test(text);
}

/** Commands bypass runTurn: auth data must never reach prompts, events or memories. */
export async function runModelCommand(text: string, runtime: ModelRuntime, ui: AuthInteraction): Promise<string | undefined> {
  if (!isModelCommand(text)) return undefined;
  const [command, arg, method, ...extra] = text.trim().split(/\s+/);
  if (extra.length || (command !== "/login" && method)
    || ((command === "/auth" || command === "/help") && arg)) {
    throw new ModelSetupError("参数过多，请使用 /help 查看格式。");
  }
  const providers = runtime.models.getProviders();
  const refresh = async (provider: string) => {
    const warning = await runtime.refreshCatalog(provider, ui.signal);
    if (warning) ui.notify({ type: "info", message: warning });
  };
  const pickProvider = async (ids: readonly string[]) => arg || ui.prompt({
    type: "select", message: "选择 Provider（Esc 取消）", options: ids.map(id => ({
      id, label: providers.find(p => p.id === id)?.name ?? id, description: id,
    })),
  });
  if (command === "/help") return MODEL_HELP;
  if (command === "/auth") {
    const saved = await runtime.credentials.list();
    return (saved.length ? saved.map(c => `${c.providerId}: ${c.type}`).join("\n") : "没有已保存的本地凭证。")
      + "\n环境变量凭证独立生效；/model 选择模型。";
  }
  if (command === "/login") {
    const id = await pickProvider(providers.filter(p => p.auth.oauth || p.auth.apiKey?.login).map(p => p.id));
    const p = runtime.models.getProvider(id);
    if (!p) throw new ModelSetupError("未知 Provider，请使用 /login 选择。");
    const methods: { id: AuthType; label: string }[] = [];
    if (p.auth.oauth) methods.push({ id: "oauth", label: p.auth.oauth.loginLabel ?? "浏览器 / 设备码授权" });
    if (p.auth.apiKey?.login) methods.push({ id: "api_key", label: "API key" });
    const type = method ?? (methods.length === 1 ? methods[0]!.id : await ui.prompt({
      type: "select", message: "选择登录方式", options: methods,
    }));
    if (type !== "oauth" && type !== "api_key") throw new ModelSetupError("不支持的登录方式。");
    await runtime.login(id, type, ui);
    await refresh(id);
    return "登录成功，凭证已保存。用 /model 选择该 Provider 的模型后开始提问。";
  }
  if (command === "/logout") {
    const saved = await runtime.credentials.list();
    if (!arg && !saved.length) return "没有可退出的本地账号。";
    const id = await pickProvider(saved.map(c => c.providerId));
    if (!runtime.models.getProvider(id)) throw new ModelSetupError("未知 Provider。");
    await runtime.models.logout(id, { signal: ui.signal });
    return "已删除该 Provider 的本地凭证；环境变量中的凭证仍独立生效。";
  }
  if (command === "/model") {
    let provider: string, model: string;
    const slash = arg?.indexOf("/") ?? -1;
    if (arg && slash > 0) { provider = arg.slice(0, slash); model = arg.slice(slash + 1); await refresh(provider); }
    else {
      provider = await pickProvider(providers.filter(p => p.getModels().length).map(p => p.id));
      await refresh(provider);
      const models = runtime.models.getModels(provider);
      if (!models.length) throw new ModelSetupError("此 Provider 没有可选模型。");
      model = await ui.prompt({ type: "select", message: "选择模型", options: models.map(m => ({
        id: m.id, label: m.name, description: m.id,
      })) });
    }
    const selected = await runtime.select(provider, model);
    return `已切换并保存模型：${selected.provider}/${selected.id}`;
  }
  if (command === "/models") {
    if (arg) await refresh(arg);
    const models = runtime.models.getModels(arg);
    if (!models.length) return "没有匹配模型。用 /model 交互选择。";
    return models.slice(0, 40).map(m => `${m.provider}/${m.id} — ${m.name}`).join("\n")
      + (models.length > 40 ? `\n共 ${models.length} 个；用 /models provider 缩小范围或 /model 选择。` : "");
  }
  return "未知命令。\n" + MODEL_HELP;
}
