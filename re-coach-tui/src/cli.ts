#!/usr/bin/env node
// 知返 Re: Coach TUI 入口
import { checkBaseUrlSecurity, loadConfig, ensureDataDir } from "./config.js";
import { Store } from "./store.js";
import { startApp } from "./ui/app.js";
import { getModelRuntime, modelCommandError } from "./model-runtime.js";
import { MODEL_HELP, runModelCommand } from "./model-commands.js";
import { cliAuthInteraction } from "./cli-auth.js";
import { sanitizeTerminalText } from "./ui/sanitize.js";

async function main(): Promise<void> {
  const cfg = loadConfig();
  const args = process.argv.slice(2);
  if (args.length) {
    if (args[0] === "--help" || args[0] === "-h") {
      process.stdout.write(MODEL_HELP.replace(/^\//gm, "recoach ") + "\n");
      return;
    }
    const controller = new AbortController();
    const cancel = () => controller.abort();
    process.once("SIGINT", cancel);
    try {
      const result = await runModelCommand("/" + args.join(" "), getModelRuntime(cfg), cliAuthInteraction(controller));
      process.stdout.write(sanitizeTerminalText(result ?? MODEL_HELP) + "\n");
    } finally { process.removeListener("SIGINT", cancel); }
    return;
  }
  if ((!process.env.RECOACH_LLM_PROVIDER || process.env.RECOACH_LLM_PROVIDER === "pi")
    && !process.env.RECOACH_LLM_MODEL) {
    await getModelRuntime(cfg).restoreSelection();
  }
  // 明文 http 会把 Authorization: Bearer <key> 发到网络上，必须显式阻断。
  const urlProblem =
    checkBaseUrlSecurity(cfg.llmBaseUrl, Boolean(cfg.llmApiKey)) ??
    checkBaseUrlSecurity(cfg.deepseekBaseUrl, Boolean(cfg.deepseekApiKey));
  if (urlProblem) {
    console.error(urlProblem);
    process.exit(1);
  }
  ensureDataDir(cfg);
  const store = new Store(cfg);
  const session = store.createSession();
  // 恢复上次会话并回放历史：每次启动都开新会话会让用户"昨天的对话不见了"。
  // 恢复失败（数据损坏/无历史）时静默从空会话开始。
  const previous = store.latestResumableSession();
  const history =
    previous && previous.id !== session.id ? store.sessionTurns(previous.id) : [];
  if (previous && history.length > 0) {
    store.deleteSession(session.id);
  }
  const active = previous && history.length > 0 ? previous.id : session.id;
  await startApp(cfg, store, {
    id: active,
    memoryOn: cfg.memoryOn,
    isFork: false,
  }, history);
}

main().catch((err) => {
  console.error(modelCommandError(err));
  process.exit(1);
});
