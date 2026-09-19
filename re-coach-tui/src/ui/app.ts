// 知返 Re: Coach TUI 主应用
import {
  Box,
  Container,
  Editor,
  Loader,
  Markdown,
  matchesKey,
  ProcessTerminal,
  SelectList,
  Spacer,
  Text,
  TuiAltScreen,
  Key,
} from "@earendil-works/pi-tui";
import type { AppConfig } from "../config.js";
import type { Store } from "../store.js";
import { runTurn } from "../orchestrator.js";
import type { AgentSession, AgentTurnEvent } from "../agent.js";
import { editorTheme, markdownTheme, selectListTheme, theme } from "./theme.js";
import { sanitizeTerminalText } from "./sanitize.js";
import type { ClarificationOption, TurnPresentation } from "../types.js";

const BRAND = "知返 Re: Coach";

/** 启动时回放的历史轮次（用户输入 + 助手正文）。 */
export interface HistoryTurn {
  userText: string;
  assistantText: string;
}

export async function startApp(
  cfg: AppConfig,
  store: Store,
  session: AgentSession,
  history: HistoryTurn[] = [],
): Promise<void> {
  const terminal = new ProcessTerminal();
  const ui = new TuiAltScreen(terminal);
  const mdTheme = markdownTheme();

  const headerContainer = new Container();
  const chatContainer = new Container();
  const statusContainer = new Container();
  const editorContainer = new Container();
  const footer = new Container();

  const memoryTag = session.isFork
    ? ` · Fork(${session.memoryMode === "on" ? "记忆开" : "记忆关"})`
    : session.memoryOn
      ? " · 记忆已开启"
      : " · 记忆已关闭";
  headerContainer.addChild(new Text(theme.header(` ${BRAND}${memoryTag}`), 0, 0));

  const editor = new Editor(ui, editorTheme());
  editor.setPaddingX(1);
  editorContainer.addChild(editor);

  let activeLoader: Loader | null = null;
  let processing = false;

  function showLoader(msg: string): void {
    hideLoader();
    activeLoader = new Loader(ui, theme.spinner, theme.loaderMsg, msg);
    statusContainer.addChild(activeLoader);
    activeLoader.start();
    ui.requestRender();
  }

  function hideLoader(): void {
    if (activeLoader) {
      activeLoader.stop();
      statusContainer.removeChild(activeLoader);
      activeLoader = null;
    }
    ui.requestRender();
  }

  function addUserMessage(text: string): void {
    chatContainer.addChild(new Spacer(1));
    const box = new Box(1, 1, theme.userBg);
    // 用户自己的输入本属可信，但粘贴内容可能夹带转义字节，统一净化做纵深防御。
    const safe = sanitizeTerminalText(text);
    box.addChild(new Markdown(theme.userText(`**你：** ${safe}`), 0, 0, mdTheme));
    chatContainer.addChild(box);
    ui.requestRender();
  }

  // 流式助手消息：维护完整正文，思考块用折叠文本
  let assistantMd: Markdown | null = null;
  let assistantBody = "";
  let thinkingBuffer = "";

  function beginAssistantMessage(): void {
    assistantBody = "";
    thinkingBuffer = "";
    chatContainer.addChild(new Spacer(1));
    assistantMd = new Markdown("", 1, 0, mdTheme);
    chatContainer.addChild(assistantMd);
    ui.requestRender();
  }

  function renderAssistant(): void {
    if (!assistantMd) return;
    const parts: string[] = [];
    if (thinkingBuffer) {
      parts.push(theme.thinking(`▸ 思考中（${thinkingBuffer.length} 字）…`));
    }
    if (assistantBody) {
      parts.push(assistantBody);
    }
    assistantMd.setText(parts.join("\n\n"));
    ui.requestRender();
  }

  function appendThinking(delta: string): void {
    // 思考块只显示字符数，本身不是渲染面；仍统一净化，避免将来改成明文展示时退化。
    thinkingBuffer += sanitizeTerminalText(delta);
    renderAssistant();
  }

  function appendContent(delta: string): void {
    thinkingBuffer = "";
    // 关键：助手正文来自 LLM 端点（不可信），pi-tui 渲染时保留 ANSI，
    // 必须在进入 assistantBody 之前剥掉控制序列（详见 ui/sanitize.ts）。
    assistantBody += sanitizeTerminalText(delta);
    renderAssistant();
  }

  function renderFooter(presentation?: TurnPresentation): void {
    footer.clear();
    const bits: string[] = [];
    if (presentation?.personalization.length) {
      bits.push(`${theme.personalization("● 已应用偏好")} ${presentation.personalization.length} 条`);
    }
    if (presentation?.metrics) {
      bits.push(`${theme.meta(`TTFT ${presentation.metrics.timeToFirstTokenMs ?? 0}ms`)}`);
      // 降级必须显式提示：否则密钥失效时用户读的是模板文本却以为来自模型。
      // 依据本轮实际值，而不是配置里的 provider（后者降级后仍然是配置值）。
      const m = presentation.metrics;
      if (m.fallback) {
        const requested = m.model || "已配置的模型";
        bits.push(
          theme.error(`⚠ 本轮为模板降级（${m.fallbackReason || "ERROR"}，原本请求 ${requested}）`),
        );
      } else if (m.provider === "template") {
        bits.push(theme.dim("模板模式"));
      }
    }
    // 截断必须提示：续写次数用尽后回答会停在一句话中间，
    // 不提示的话用户会以为模型就说到这里为止。
    if (presentation?.truncated) {
      bits.push(theme.error("⚠ 回答不完整（输出长度受限，已被截断）"));
    }
    footer.addChild(
      new Text(
        theme.dim(bits.length ? bits.join("   ") : "输入问题，或输入偏好让 Re: Coach 记住"),
        1,
        0,
      ),
    );
    ui.requestRender();
  }

  function showClarification(options: ClarificationOption[]): void {
    // label/detail/followUp 同样来自服务端演示数据，渲染前净化。
    const items = options.map((o) => ({
      value: sanitizeTerminalText(o.followUp),
      label: sanitizeTerminalText(o.label),
      description: sanitizeTerminalText(o.detail ?? ""),
    }));
    const list = new SelectList(items, 6, selectListTheme());
    const done = () => {
      editorContainer.clear();
      editorContainer.addChild(editor);
      ui.setFocus(editor);
      ui.requestRender();
    };
    list.onSelect = (item) => {
      done();
      void submit(item.value);
    };
    list.onCancel = () => done();
    editorContainer.clear();
    editorContainer.addChild(list);
    ui.setFocus(list);
    ui.requestRender();
  }

  function handleEvent(e: AgentTurnEvent): void {
    switch (e.type) {
      case "turn.started":
        hideLoader();
        showLoader(e.mode === "clarify" ? "正在判断你的学习卡点…" : "知返正在讲解…");
        break;
      case "assistant.thinking":
        appendThinking(e.delta);
        break;
      case "assistant.delta":
        if (!assistantMd) beginAssistantMessage();
        appendContent(e.delta);
        break;
      case "turn.completed":
        hideLoader();
        if (e.presentation.mode === "clarify" && e.presentation.clarificationOptions?.length) {
          showClarification(e.presentation.clarificationOptions);
        }
        renderFooter(e.presentation);
        break;
      case "turn.error":
        hideLoader();
        if (!assistantMd) beginAssistantMessage();
        assistantBody += `\n${theme.error("⚠ " + sanitizeTerminalText(e.message))}`;
        renderAssistant();
        break;
    }
  }

  async function submit(raw: string): Promise<void> {
    if (processing) return;
    const text = raw.trim();
    if (!text) return;
    processing = true;
    try {
      addUserMessage(text);
      editor.setText("");
      assistantMd = null;
      beginAssistantMessage();
      showLoader("知返正在思考…");
      await runTurn({ cfg, store, session, userText: text, onEvent: handleEvent });
    } finally {
      processing = false;
      ui.requestRender();
    }
  }

  editor.onSubmit = (text) => {
    void submit(text);
  };

  ui.addChild(headerContainer);
  ui.addChild(chatContainer);
  ui.addChild(statusContainer);
  ui.addChild(editorContainer);
  ui.addChild(footer);
  ui.setFocus(editor);

  chatContainer.addChild(new Spacer(1));
  if (history.length > 0) {
    // 回放上次会话：终端里"昨天的对话不见了"和网页里刷新丢历史是同一类问题。
    chatContainer.addChild(
      new Markdown(
        theme.dim(`—— 继续上次的对话（${history.length} 轮），下面是历史记录 ——`),
        0,
        0,
        mdTheme,
      ),
    );
    for (const turn of history) {
      addUserMessage(turn.userText);
      const box = new Box(1, 1);
      box.addChild(new Markdown(sanitizeTerminalText(turn.assistantText), 0, 0, mdTheme));
      chatContainer.addChild(box);
      chatContainer.addChild(new Spacer(1));
    }
    chatContainer.addChild(
      new Markdown(theme.dim("—— 以上为历史记录，继续提问即可 ——"), 0, 0, mdTheme),
    );
  } else {
    chatContainer.addChild(
      new Markdown(
        `${theme.header("你好，我是知返")} —— 你的机器学习/深度学习学习教练。\n\n` +
          theme.dim("我可以记住你的学习偏好，并调整讲解方式。告诉我你想学什么概念，或直接告诉我一个偏好，例如：\n\n") +
          "- " + theme.accent("「讲 X 的时候先给公式」") + theme.dim("  → 我会长期记住\n") +
          "- " + theme.accent("「我不懂 反向传播」") + theme.dim("  → 我会先澄清你的卡点\n") +
          "- " + theme.accent("「这个太长了，简短点」") + theme.dim("  → 我会调整篇幅"),
        0,
        0,
        mdTheme,
      ),
    );
  }
  chatContainer.addChild(new Spacer(1));

  ui.addInputListener((data) => {
    if (matchesKey(data, Key.ctrl("c"))) {
      ui.stop();
      process.exit(0);
    }
    return undefined;
  });

  ui.start();
}
