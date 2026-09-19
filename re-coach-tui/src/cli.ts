#!/usr/bin/env node
// 知返 Re: Coach TUI 入口
import { checkBaseUrlSecurity, loadConfig, ensureDataDir } from "./config.js";
import { Store } from "./store.js";
import { startApp } from "./ui/app.js";

async function main(): Promise<void> {
  const cfg = loadConfig();
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
  const session = store.createSession(cfg.locale);
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
  console.error(err);
  process.exit(1);
});
