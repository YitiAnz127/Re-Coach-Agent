import {
  AlertTriangle,
  Brain,
  Clock3,
  Database,
  GitFork,
  LoaderCircle,
  Play,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  AgentApiError,
  createFairAbFork,
  type FairAbFork,
  streamTurn,
} from "../services/agent-client";
import type { TurnPresentation } from "../types";
import { FormulaText } from "./FormulaText";

type FixedMemoryMode = "on" | "off";
type ForkRunState = "idle" | "forking" | "running" | "complete" | "partial" | "error";
type BranchStatus = "idle" | "waiting" | "streaming" | "complete" | "error";

interface ForkBranchState {
  mode: FixedMemoryMode;
  sessionId?: string;
  status: BranchStatus;
  content: string;
  thinking: string;
  focus?: string;
  presentation?: TurnPresentation;
  elapsedMs?: number;
  error?: string;
}

interface FairForkComparisonProps {
  sourceSessionId?: string;
  available: boolean;
  disabled?: boolean;
}

function emptyBranches(status: BranchStatus = "idle"): Record<FixedMemoryMode, ForkBranchState> {
  return {
    on: { mode: "on", status, content: "", thinking: "" },
    off: { mode: "off", status, content: "", thinking: "" },
  };
}

function formatDuration(value?: number) {
  if (value === undefined) return "—";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

function safeErrorMessage(error: unknown) {
  if (error instanceof AgentApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "公平对照暂时无法完成，请稍后重试。";
}

function branchStatusText(status: BranchStatus) {
  switch (status) {
    case "waiting":
      return "准备分支";
    case "streaming":
      return "正在回答";
    case "complete":
      return "已完成";
    case "error":
      return "未完成";
    default:
      return "等待运行";
  }
}

function ForkBranch({ branch }: { branch: ForkBranchState }) {
  const memoryOn = branch.mode === "on";
  const evidence = branch.presentation?.personalization ?? [];
  return (
    <article className={`fork-branch fork-branch-${branch.mode}`}>
      <header className="fork-branch-header">
        <span className="fork-branch-letter">{memoryOn ? "A" : "B"}</span>
        <div className="fork-branch-title">
          <h3>{memoryOn ? "记忆开启" : "记忆关闭"}</h3>
          <p>{memoryOn ? "仅读取创建 Fork 时冻结的记忆快照" : "完全跳过长期记忆读取"}</p>
        </div>
        <span className={`fork-branch-status is-${branch.status}`}>
          {branch.status === "streaming" || branch.status === "waiting" ? (
            <LoaderCircle aria-hidden="true" size={13} />
          ) : memoryOn ? (
            <Brain aria-hidden="true" size={13} />
          ) : (
            <ShieldCheck aria-hidden="true" size={13} />
          )}
          {branchStatusText(branch.status)}
        </span>
      </header>

      <dl className="fork-branch-metrics" aria-label={`${memoryOn ? "记忆开启" : "记忆关闭"}分支指标`}>
        <div>
          <dt><Database aria-hidden="true" size={14} />个性化依据</dt>
          <dd>{branch.presentation ? `${evidence.length} 条` : "—"}</dd>
        </div>
        <div>
          <dt><Clock3 aria-hidden="true" size={14} />首字延迟</dt>
          <dd>{formatDuration(branch.presentation?.metrics.timeToFirstTokenMs)}</dd>
        </div>
        <div>
          <dt><GitFork aria-hidden="true" size={14} />端到端</dt>
          <dd>{formatDuration(branch.elapsedMs)}</dd>
        </div>
      </dl>

      <section className="fork-branch-answer" aria-label={`${memoryOn ? "记忆开启" : "记忆关闭"}回答`}>
        {branch.error ? (
          <div className="fork-branch-error" role="alert">
            <AlertTriangle aria-hidden="true" size={17} />
            <div>
              <strong>这个分支没有完成</strong>
              <p>{branch.error}</p>
            </div>
          </div>
        ) : branch.content || branch.thinking ? (
          <>
            {branch.thinking && (
              <details className="thinking-details" open={!branch.content}>
                <summary>{branch.content ? "查看本轮思考" : "正在思考…"}</summary>
                <div className="thinking-copy">{branch.thinking}</div>
              </details>
            )}
            {branch.content && <FormulaText content={branch.content} />}
          </>
        ) : branch.status === "waiting" || branch.status === "streaming" ? (
          <div className="fork-branch-thinking" role="status">
            <span />
            {branch.focus ? `正在处理：${branch.focus}` : "正在从同一快照生成回答"}
          </div>
        ) : (
          <p className="fork-branch-placeholder">运行后，这里会显示该分支的真实回答。</p>
        )}
      </section>

      {branch.presentation && (
        <section className="fork-branch-evidence" aria-label="分支长期记忆依据">
          <h4>{memoryOn ? "冻结快照内实际带入的记忆" : "长期记忆边界"}</h4>
          {evidence.length > 0 ? (
            <div>
              {evidence.map((item) => (
                <article key={item.memoryId}>
                  <strong>{item.label}</strong>
                  <p>{item.effect}</p>
                </article>
              ))}
            </div>
          ) : (
            <p className="fork-branch-evidence-empty">
              {memoryOn
                ? "快照中没有命中可应用的长期记忆。"
                : "本分支没有读取或写入任何长期记忆。"}
            </p>
          )}
        </section>
      )}
    </article>
  );
}

export function FairForkComparison({
  sourceSessionId,
  available,
  disabled = false,
}: FairForkComparisonProps) {
  const [question, setQuestion] = useState("");
  const [runState, setRunState] = useState<ForkRunState>("idle");
  const [branches, setBranches] = useState(() => emptyBranches());
  const [requestError, setRequestError] = useState<string>();
  const controllerRef = useRef<AbortController | null>(null);

  const running = runState === "forking" || runState === "running";
  const canRun = Boolean(sourceSessionId && available && question.trim() && !disabled && !running);

  useEffect(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setRunState("idle");
    setBranches(emptyBranches());
    setRequestError(undefined);
  }, [sourceSessionId]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  function patchBranch(mode: FixedMemoryMode, patch: Partial<ForkBranchState>) {
    setBranches((current) => ({
      ...current,
      [mode]: { ...current[mode], ...patch },
    }));
  }

  async function runBranch(fork: FairAbFork, content: string, signal: AbortSignal) {
    const mode = fork.memoryMode;
    const startedAt = performance.now();
    patchBranch(mode, { sessionId: fork.sessionId, status: "streaming" });
    try {
      for await (const event of streamTurn({
        sessionId: fork.sessionId,
        content,
        clientTurnId: `client_fork_${mode}_${crypto.randomUUID()}`,
        signal,
      })) {
        if (event.type === "turn.started") {
          patchBranch(mode, { focus: event.focus });
        } else if (event.type === "assistant.thinking") {
          setBranches((current) => ({
            ...current,
            [mode]: {
              ...current[mode],
              thinking: current[mode].thinking + event.delta,
            },
          }));
        } else if (event.type === "assistant.delta") {
          setBranches((current) => ({
            ...current,
            [mode]: {
              ...current[mode],
              content: current[mode].content + event.delta,
            },
          }));
        } else if (event.type === "turn.completed") {
          patchBranch(mode, {
            status: "complete",
            presentation: event.presentation,
            elapsedMs: Math.max(0, Math.round(performance.now() - startedAt)),
          });
        } else if (event.type === "turn.error") {
          throw new AgentApiError(event.message, event.code, event.retryable, event.requestId);
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      patchBranch(mode, {
        status: "error",
        elapsedMs: Math.max(0, Math.round(performance.now() - startedAt)),
        error: safeErrorMessage(error),
      });
      throw error;
    }
  }

  async function runFairFork() {
    const content = question.trim();
    if (!canRun || !sourceSessionId || !content) return;

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setRunState("forking");
    setBranches(emptyBranches("waiting"));
    setRequestError(undefined);

    try {
      const group = await createFairAbFork(sourceSessionId, controller.signal);
      if (controller.signal.aborted) return;
      setRunState("running");
      setBranches((current) => {
        const next = { ...current };
        for (const fork of group.forks) {
          next[fork.memoryMode] = {
            ...next[fork.memoryMode],
            sessionId: fork.sessionId,
          };
        }
        return next;
      });

      const results = await Promise.allSettled(
        group.forks.map((fork) => runBranch(fork, content, controller.signal)),
      );
      if (controller.signal.aborted) return;
      const failures = results.filter((result) => result.status === "rejected").length;
      setRunState(failures === 0 ? "complete" : failures === results.length ? "error" : "partial");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setRequestError(safeErrorMessage(error));
      setRunState("error");
      setBranches((current) => ({
        on: current.on.status === "waiting" ? { ...current.on, status: "error", error: safeErrorMessage(error) } : current.on,
        off: current.off.status === "waiting" ? { ...current.off, status: "error", error: safeErrorMessage(error) } : current.off,
      }));
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  const availabilityText = !available
    ? "当前后端未声明 Fair Fork 能力"
    : !sourceSessionId
      ? "先在 Chat 完成至少一轮对话，作为共同基线"
      : "在您点击后，系统才会复制当前 Session 并运行两个隔离分支";

  return (
    <section className="fair-fork-section" aria-labelledby="fair-fork-heading">
      <header className="fair-fork-heading">
        <div>
          <h2 id="fair-fork-heading">公平分支对比</h2>
          <p>在您需要时，我们将冻结当前会话，用您提供的同一问题并行生成“记忆开启”与“记忆关闭”两个隔离结果。</p>
        </div>
        <span className="fair-fork-lock-state">
          <GitFork aria-hidden="true" size={15} />
          按需运行 · 只读快照
        </span>
      </header>

      <div className="unified-comparison-panel glass-surface">
        <form
          className="fork-launcher"
          onSubmit={(event) => {
            event.preventDefault();
            void runFairFork();
          }}
        >
          <label htmlFor="fork-question">
            <span className="fork-launcher-icon" aria-hidden="true"><GitFork size={19} /></span>
            <span>
               <strong>输入要公平比较的问题</strong>
              <small>{availabilityText}</small>
            </span>
          </label>
          <textarea
            id="fork-question"
            rows={2}
            maxLength={4000}
            value={question}
            disabled={disabled || running || !available}
            placeholder="例如：请用一个数值例子解释反向传播中的链式法则。"
            onChange={(event) => setQuestion(event.target.value)}
          />
          <div className="fork-launcher-actions">
            <span className="fork-launcher-note" aria-live="polite">
              {runState === "forking"
                ? "正在冻结会话快照…"
                : runState === "running"
                  ? "两个分支正在并行回答…"
                  : runState === "complete"
                    ? "两个分支均已完成"
                    : runState === "partial"
                      ? "一个分支已完成，请查看错误信息"
                    : "Fork 分支不会把新偏好写回长期记忆"}
            </span>
            <button type="submit" disabled={!canRun}>
              {running ? (
                <><LoaderCircle className="fork-spinner" aria-hidden="true" size={16} />对比中</>
              ) : runState === "complete" || runState === "partial" || runState === "error" ? (
                <><RotateCcw aria-hidden="true" size={16} />重新对比</>
              ) : (
                <><Play aria-hidden="true" size={16} />开始 Fork 对比</>
              )}
            </button>
          </div>
          {requestError && <p className="fork-launcher-error" role="alert">{requestError}</p>}
        </form>

        {runState !== "idle" && (
          <div className="fork-result">
            <div className="fork-branches">
              <ForkBranch branch={branches.on} />
              <ForkBranch branch={branches.off} />
            </div>
            <footer className="fork-result-footer">
              <GitFork aria-hidden="true" size={17} />
              <p>
                本次结果由 Fair Fork 生成：两组共享创建时的 Session Brief、可见对话与状态快照；外部模型随机性仍可能造成差异。
              </p>
            </footer>
          </div>
        )}
      </div>
    </section>
  );
}
