import { Activity, MessageSquare, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { FairForkComparison } from "./components/FairForkComparison";
import { ModelBadge } from "./components/ModelBadge";
import { SideRail } from "./components/SideRail";
import {
  AgentApiError,
  agentClientConfig,
  createSession,
  fetchSessionTurns,
  getServiceMeta,
  streamTurn,
} from "./services/agent-client";
import type { RestoredTurn } from "./services/agent-client";
import type { ServiceMeta } from "./services/service-meta";
import { prepareAssistantRetry } from "./services/turn-retry";
import type { ChatMessage, TurnPresentation } from "./types";

type ViewMode = "chat" | "performance";

/** localStorage 键：记住上次会话，刷新后可恢复。 */
const SESSION_STORAGE_KEY = "recoach.lastSessionId";

function readStoredSessionId(): string | null {
  try {
    return window.localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null; // 隐私模式等：当作没有历史
  }
}

function clearStoredSessionId(): void {
  try {
    window.localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    /* 忽略 */
  }
}

/** 把服务端返回的一轮历史映射成两条可渲染的消息。 */
function restoredTurnToMessages(turn: RestoredTurn): ChatMessage[] {
  const at = turn.createdAt ?? "";
  return [
    {
      id: `restored-user-${turn.turnId}`,
      role: "user",
      content: turn.userText,
      state: "complete",
      turnId: turn.turnId,
      completedAt: at,
    },
    {
      id: `restored-assistant-${turn.turnId}`,
      role: "assistant",
      content: turn.assistantText,
      state: "complete",
      presentation: turn.presentation,
      turnId: turn.turnId,
      completedAt: at,
    },
  ];
}

interface ExecuteTurnInput {
  content: string;
  clientTurnId: string;
  assistantId: string;
  appendMessages: boolean;
}

function EmptyState() {
  return (
    <section className="empty-state" aria-labelledby="page-title">
      <h1 id="page-title">Make complex ideas click.</h1>
    </section>
  );
}

function createId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [sessionId, setSessionId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [activePlan, setActivePlan] = useState<string[]>([]);
  const [activeFocus, setActiveFocus] = useState("尚未开始");
  const [viewMode, setViewMode] = useState<ViewMode>("chat");
  const [serviceMeta, setServiceMeta] = useState<ServiceMeta>();
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  // 一旦本轮对话已经开始，就不允许挂载时的历史恢复再覆盖它
  const sessionRef = useRef(false);
  useEffect(() => {
    if (sessionId || messages.length > 0) sessionRef.current = true;
  }, [sessionId, messages.length]);

  const latestPresentation = useMemo<TurnPresentation | undefined>(() => {
    return [...messages].reverse().find((message) => message.presentation)?.presentation;
  }, [messages]);

  useEffect(() => {
    const controller = new AbortController();
    void getServiceMeta(controller.signal)
      .then(setServiceMeta)
      .catch(() => setServiceMeta(undefined));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (messages.length === 0) return;
    endRef.current?.scrollIntoView({ behavior: busy ? "smooth" : "auto", block: "end" });
  }, [busy, messages]);

  // 会话 id 落 localStorage。刷新后不再丢失整段对话。
  useEffect(() => {
    if (!sessionId) return;
    try {
      window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    } catch {
      // 隐私模式等场景下 localStorage 不可用：不影响本次会话，只是刷新后不恢复
    }
  }, [sessionId]);

  // 首次挂载时尝试恢复上次会话。
  // 恢复失败（会话被清理 / 数据被删 / 后端版本不支持）时清掉这个 id 并开新会话，
  // 否则每次刷新都会重试一个失效的 id。
  useEffect(() => {
    const saved = readStoredSessionId();
    if (!saved) return;
    const controller = new AbortController();
    void fetchSessionTurns(saved, controller.signal)
      .then((turns) => {
        // 用户在恢复完成前已经发了消息：不要用历史覆盖当前对话
        if (controller.signal.aborted || sessionRef.current || turns.length === 0) return;
        setSessionId(saved);
        setMessages(turns.flatMap(restoredTurnToMessages));
      })
      .catch((error) => {
        // 卸载、刷新或 StrictMode 的开发期 effect 重放都会主动取消请求。
        // 取消不代表会话失效，不能因此删除仍可恢复的 sessionId。
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        clearStoredSessionId();
      });
    return () => controller.abort();
    // 仅在挂载时执行一次；sessionRef 保证不与用户操作竞争
  }, []);


  const executeTurn = useCallback(async ({
    content,
    clientTurnId,
    assistantId,
    appendMessages,
  }: ExecuteTurnInput) => {
    if (abortRef.current) return;

    const controller = new AbortController();
    const startedAt = performance.now();
    abortRef.current = controller;
    setBusy(true);
    setPrompt("");
    setActivePlan([]);

    if (appendMessages) {
      const userMessage: ChatMessage = {
        id: createId("user"),
        role: "user",
        content,
        state: "complete",
      };
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        state: "streaming",
        clientTurnId,
        requestContent: content,
        retryable: true,
      };
      setMessages((current) => [...current, userMessage, assistantMessage]);
    } else {
      setMessages((current) => prepareAssistantRetry(current, assistantId).messages);
    }

    try {
      const currentSession = sessionId ?? (await createSession(controller.signal));
      if (controller.signal.aborted || abortRef.current !== controller) return;
      if (!sessionId) setSessionId(currentSession);

      for await (const event of streamTurn({
        sessionId: currentSession,
        content,
        clientTurnId,
        signal: controller.signal,
      })) {
        if (controller.signal.aborted || abortRef.current !== controller) return;
        if (event.type === "turn.started") {
          setActiveFocus(event.focus);
          setActivePlan(event.plan);
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId ? { ...message, turnId: event.turnId } : message,
            ),
          );
        }

        if (event.type === "assistant.thinking") {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, thinking: (message.thinking ?? "") + event.delta }
                : message,
            ),
          );
        }

        if (event.type === "assistant.delta") {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + event.delta }
                : message,
            ),
          );
        }

        if (event.type === "turn.completed") {
          setActiveFocus(event.presentation.focus);
          setActivePlan(event.presentation.plan);
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    state: "complete",
                    presentation: event.presentation,
                    retryable: false,
                    errorCode: undefined,
                    turnId: event.turnId,
                    elapsedMs: Math.max(0, Math.round(performance.now() - startedAt)),
                    completedAt: new Date().toISOString(),
                  }
                : message,
            ),
          );
        }

        if (event.type === "turn.error") {
          throw new AgentApiError(event.message, event.code, event.retryable, event.requestId);
        }
      }
    } catch (error) {
      if (controller.signal.aborted || abortRef.current !== controller) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      const apiError =
        error instanceof AgentApiError
          ? error
          : new AgentApiError(
              error instanceof Error ? error.message : "讲解服务暂时不可用，请稍后再试。",
            );
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                state: "error",
                content: apiError.message,
                retryable: apiError.retryable,
                errorCode: apiError.code,
                requestId: apiError.requestId,
              }
            : message,
        ),
      );
    } finally {
      if (abortRef.current === controller) {
        setBusy(false);
        abortRef.current = null;
      }
    }
  }, [sessionId]);

  const sendPrompt = useCallback((nextPrompt = prompt) => {
    const content = nextPrompt.trim();
    if (!content || busy) return;
    void executeTurn({
      content,
      clientTurnId: createId("client"),
      assistantId: createId("assistant"),
      appendMessages: true,
    });
  }, [prompt, busy, executeTurn]);

  const retryAssistant = useCallback((assistantId: string) => {
    if (busy) return;
    let retry;
    try {
      retry = prepareAssistantRetry(messages, assistantId);
    } catch {
      return;
    }
    void executeTurn({
      content: retry.content,
      clientTurnId: retry.clientTurnId,
      assistantId: retry.assistantId,
      appendMessages: false,
    });
  }, [busy, messages, executeTurn]);

  const startNewSession = useCallback(() => {
    // 历史恢复可能仍在飞行中。先关闭它的提交资格，避免用户点击 New 后
    // 延迟返回的旧会话重新覆盖空白的新会话。
    sessionRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setSessionId(undefined);
    setPrompt("");
    setBusy(false);
    setActivePlan([]);
    setActiveFocus("尚未开始");
    // 必须同时清掉持久化的会话 id，否则刷新后又会把刚离开的旧会话拉回来
    clearStoredSessionId();
  }, []);

  const switchToChat = useCallback(() => setViewMode("chat"), []);
  const switchToPerformance = useCallback(() => setViewMode("performance"), []);

  const hasConversation = messages.length > 0;

  return (
    <div className={hasConversation ? "app has-conversation" : "app"}>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <header className="topbar">
        <div className="topbar-grid">
          <button className="brand" type="button" onClick={startNewSession} aria-label="返回知返首页">
            <img src="/recoach-mark.svg" alt="" />
            <span>
              <strong>知返</strong>
              <small>Re:Coach</small>
            </span>
          </button>
          {/*
            当前模型常驻显示：以前只有"出问题（降级）"才提示，
            正常状态要切到 Performance 标签页才看得到在用哪个模型。
            放在顶栏是始终可见的位置，用户随时能确认自己是在和模型对话还是模板。
          */}
          <ModelBadge presentation={latestPresentation} serviceMeta={serviceMeta} />
        </div>
      </header>

      <main id="main-content" className="main-grid">
        <div className="side-nav">
          <div className="view-switcher" role="group" aria-label="视图切换">
            <button
              type="button"
              aria-pressed={viewMode === "chat"}
              className={`view-tab ${viewMode === "chat" ? "active" : ""}`}
              onClick={switchToChat}
            >
              <MessageSquare aria-hidden="true" size={17} strokeWidth={1.8} />
              Chat
            </button>
            <button
              type="button"
              aria-pressed={viewMode === "performance"}
              className={`view-tab ${viewMode === "performance" ? "active" : ""}`}
              onClick={switchToPerformance}
            >
              <Activity aria-hidden="true" size={17} strokeWidth={1.8} />
              Performance
            </button>
          </div>

          <div className="side-nav-section">
            <button className="new-chat-btn" type="button" onClick={startNewSession}>
              <Plus aria-hidden="true" size={17} />
              New
            </button>
          </div>
        </div>

        {viewMode === "chat" ? (
          <div className="learning-column">
            {!hasConversation ? (
              <EmptyState />
            ) : (
              <Conversation
                messages={messages}
                onAction={sendPrompt}
                onRetry={retryAssistant}
                serviceMeta={serviceMeta}
              />
            )}
            <Composer
              value={prompt}
              busy={busy}
              onChange={setPrompt}
              onSubmit={() => sendPrompt()}
            />
            <div ref={endRef} />
          </div>
        ) : (
          <div className="performance-column">
            <div className="performance-stack">
              <SideRail
                presentation={latestPresentation}
                activePlan={activePlan}
                demoMode={agentClientConfig.demoMode}
                serviceMeta={serviceMeta}
              />
              <FairForkComparison
                sourceSessionId={sessionId}
                available={serviceMeta?.fairAbFork ?? false}
                disabled={busy}
              />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
