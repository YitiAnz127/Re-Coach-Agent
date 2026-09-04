import { AlertTriangle, ArrowRight, Check, FlaskConical, RotateCcw } from "lucide-react";
import { FormulaText } from "./FormulaText";
import type {
  ChatMessage,
  ClarificationOption,
  MicroExperiment,
  Retrospective,
  SuggestedAction,
} from "../types";

interface ConversationProps {
  messages: ChatMessage[];
  onAction: (prompt: string) => void;
  onRetry: (messageId: string) => void;
}

function ClarificationChoices({
  options,
  onAction,
}: {
  options: ClarificationOption[];
  onAction: (prompt: string) => void;
}) {
  return (
    <div className="clarification-choices" aria-label="选择最接近的卡点">
      {options.map((option, index) => (
        <button key={option.id} type="button" onClick={() => onAction(option.followUp)}>
          <span className="choice-index">{String.fromCharCode(65 + index)}</span>
          <span className="choice-copy">
            <strong>{option.label}</strong>
            <small>{option.detail}</small>
          </span>
          <ArrowRight aria-hidden="true" size={17} strokeWidth={1.8} />
        </button>
      ))}
    </div>
  );
}

function Experiment({ experiment }: { experiment: MicroExperiment }) {
  const logs = experiment.points.map((point) => Math.log10(Math.max(point.value, 1e-8)));
  const min = Math.min(...logs);
  const max = Math.max(...logs);

  return (
    <section className="experiment" aria-labelledby="experiment-title">
      <header>
        <span className="experiment-icon" aria-hidden="true">
          <FlaskConical size={17} strokeWidth={1.8} />
        </span>
        <div>
          <h3 id="experiment-title">微型实验 · {experiment.title}</h3>
        </div>
      </header>
      <p className="experiment-description">{experiment.description}</p>
      <div
        className="experiment-chart"
        role="img"
        aria-label="对数比例条形图：路径越深，总梯度越小"
      >
        {experiment.points.map((point, index) => {
          const width = max === min ? 100 : 12 + ((logs[index] - min) / (max - min)) * 88;
          return (
            <div className="chart-row" key={point.label}>
              <span className="chart-label">{point.label}</span>
              <span className="chart-track">
                <span className="chart-bar" style={{ width: `${width}%` }} />
              </span>
              <strong>{point.displayValue}</strong>
            </div>
          );
        })}
      </div>
      <p className="chart-scale">条宽使用对数比例，以便同时看清很小的数值。</p>
      <p className="experiment-takeaway">{experiment.takeaway}</p>
      <details className="experiment-code">
        <summary>查看实验代码</summary>
        <pre>
          <code>{experiment.code}</code>
        </pre>
      </details>
    </section>
  );
}

function RetrospectiveBlock({ retrospective }: { retrospective: Retrospective }) {
  return (
    <section className="retrospective" aria-labelledby="retrospective-title">
      <header>
        <span aria-hidden="true">
          <Check size={17} strokeWidth={2} />
        </span>
        <div>
          <h3 id="retrospective-title">本轮复盘</h3>
        </div>
      </header>
      <div className="retrospective-grid">
        <div>
          <h4>建立的连接</h4>
          <ul>
            {retrospective.connections.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4>仍未展开</h4>
          <ul>
            {retrospective.openQuestions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4>本次讲法</h4>
          <ul>
            {retrospective.approach.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>
      <p className="retrospective-boundary">这里只记录可观察事实，是否学会由你判断。</p>
    </section>
  );
}

function SuggestedActions({
  actions,
  onAction,
}: {
  actions: SuggestedAction[];
  onAction: (prompt: string) => void;
}) {
  if (actions.length === 0) return null;
  return (
    <div className="suggested-actions" aria-label="继续学习">
      {actions.map((action) => (
        <button key={action.id} type="button" onClick={() => onAction(action.prompt)}>
          {action.label}
          <ArrowRight aria-hidden="true" size={15} strokeWidth={1.8} />
        </button>
      ))}
    </div>
  );
}

function AssistantMessage({
  message,
  onAction,
  onRetry,
}: {
  message: ChatMessage;
  onAction: (prompt: string) => void;
  onRetry: (messageId: string) => void;
}) {
  const presentation = message.presentation;
  return (
    <article className="message assistant-message" aria-live={message.state === "streaming" ? "polite" : "off"}>
      <div className="assistant-meta">
        <span className="assistant-name">知返</span>
        {presentation?.depth && <span className="depth-label">{presentation.depth}</span>}
        {presentation?.focus && <span className="focus-label">{presentation.focus}</span>}
      </div>
      {message.state === "error" ? (
        <div className="error-bubble" role="alert">
          <div className="error-bubble-title">
            <AlertTriangle aria-hidden="true" size={17} />
            <strong>这次没有完成讲解</strong>
          </div>
          <p>{message.content || "讲解服务暂时不可用，请重试。"}</p>
          {(message.errorCode || message.requestId) && (
            <details className="error-details">
              <summary>详细信息</summary>
              {message.errorCode && <div>错误码：{message.errorCode}</div>}
              {message.requestId && <div>请求号：{message.requestId}</div>}
            </details>
          )}
          {message.retryable !== false && (
            <button className="retry-button" type="button" onClick={() => onRetry(message.id)}>
              <RotateCcw aria-hidden="true" size={16} />
              重新发送
            </button>
          )}
        </div>
      ) : message.state === "streaming" && !message.content && !message.thinking ? (
        <div className="thinking-line" role="status">
          <span />
          正在确定最短的讲解路径
        </div>
      ) : (
        <>
          {message.thinking ? (
            <details className="thinking-details" open={!message.content}>
              <summary>思考中…</summary>
              <div className="thinking-copy">{message.thinking}</div>
            </details>
          ) : null}
          {message.content ? (
            <div className="assistant-copy">
              <FormulaText content={message.content} />
            </div>
          ) : null}
          {presentation?.truncated && (
            <div className="truncated-notice" role="status">
              回答不完整：输出长度受限，内容可能被截断。可换一种更聚焦的问法，或点击重新发送。
            </div>
          )}
        </>
      )}
      {presentation?.clarificationOptions && (
        <ClarificationChoices options={presentation.clarificationOptions} onAction={onAction} />
      )}
      {presentation?.experiment && <Experiment experiment={presentation.experiment} />}
      {presentation?.retrospective && <RetrospectiveBlock retrospective={presentation.retrospective} />}
      {presentation && <SuggestedActions actions={presentation.suggestedActions} onAction={onAction} />}
    </article>
  );
}

export function Conversation({ messages, onAction, onRetry }: ConversationProps) {
  return (
    <section className="conversation" aria-label="学习对话">
      {messages.map((message) =>
        message.role === "user" ? (
          <article className="message user-message" key={message.id}>
            <span className="sr-only">你：</span>
            <p>{message.content}</p>
          </article>
        ) : (
          <AssistantMessage
            key={message.id}
            message={message}
            onAction={onAction}
            onRetry={onRetry}
          />
        ),
      )}
    </section>
  );
}
