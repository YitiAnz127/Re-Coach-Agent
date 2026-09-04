import { Activity, MessageSquare, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { FairForkComparison } from "./components/FairForkComparison";
import { SideRail } from "./components/SideRail";
import {
  AgentApiError,
  agentClientConfig,
  createSession,
  getServiceMeta,
  streamTurn,
} from "./services/agent-client";
import type { ServiceMeta } from "./services/service-meta";
import { prepareAssistantRetry } from "./services/turn-retry";
import type { ChatMessage, TurnPresentation } from "./types";

type ViewMode = "chat" | "performance";

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

  const executeTurn = useCallback(async ({
    content,
    clientTurnId,
    assistantId,
    appendMessages,
  }: ExecuteTurnInput) => {
    if (busy) return;

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
      if (!sessionId) setSessionId(currentSession);

      for await (const event of streamTurn({
        sessionId: currentSession,
        content,
        clientTurnId,
        signal: controller.signal,
      })) {
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
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, sessionId]);

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
    abortRef.current?.abort();
    setMessages([]);
    setSessionId(undefined);
    setPrompt("");
    setBusy(false);
    setActivePlan([]);
    setActiveFocus("尚未开始");
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
