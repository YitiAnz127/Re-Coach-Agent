import {
  Activity,
  ChevronDown,
  Clock3,
  Database,
  Gauge,
  Sparkles,
} from "lucide-react";
import {
  formatServiceProvider,
  type ServiceMeta,
} from "../services/service-meta";
import type { PerformanceMetrics, TurnPresentation } from "../types";

interface SideRailProps {
  presentation?: TurnPresentation;
  activePlan: string[];
  demoMode: boolean;
  serviceMeta?: ServiceMeta;
}

const emptyMetrics: Array<{ label: string; value: string; icon: typeof Clock3 }> = [
  { label: "首字延迟", value: "—", icon: Clock3 },
  { label: "记忆检索", value: "—", icon: Database },
  { label: "上下文编译", value: "—", icon: Gauge },
  { label: "Capsule", value: "—", icon: Activity },
];

function metricItems(metrics?: PerformanceMetrics) {
  if (!metrics) return emptyMetrics;
  return [
    { label: "首字延迟", value: `${metrics.timeToFirstTokenMs} ms`, icon: Clock3 },
    { label: "记忆检索", value: `${metrics.memorySearchMs} ms`, icon: Database },
    { label: "上下文编译", value: `${metrics.contextCompileMs} ms`, icon: Gauge },
    { label: "Capsule", value: `${metrics.memoryCapsuleTokens} tok`, icon: Activity },
  ];
}

function isInternalWildcardScope(scope: string) {
  return /^[*/\s]+$/.test(scope);
}

export function SideRail({
  presentation,
  activePlan,
  demoMode,
  serviceMeta,
}: SideRailProps) {
  const plan = presentation?.plan ?? activePlan;
  const evidence = presentation?.personalization ?? [];
  const personalizationIncrement = presentation
    ? presentation.metrics.memorySearchMs + presentation.metrics.contextCompileMs
    : null;

  return (
    <div className="side-rail-content">
      <section className="rail-panel glass-surface" aria-labelledby="evidence-heading">
        <header className="rail-panel-header">
          <span className="rail-icon" aria-hidden="true">
            <Sparkles size={17} strokeWidth={1.8} />
          </span>
          <div>
            <h2 id="evidence-heading">本次个性化依据</h2>
          </div>
        </header>

        {evidence.length > 0 ? (
          <div className="evidence-list">
            {evidence.map((item) => (
              <article className="evidence-item" key={item.memoryId}>
                <div className="evidence-title-row">
                  <h3>{item.label}</h3>
                  {!isInternalWildcardScope(item.scope) && <span>{item.scope}</span>}
                </div>
                <p>{item.effect}</p>
              </article>
            ))}
          </div>
        ) : (
          <p className="rail-empty">
            {presentation?.mode === "clarify"
              ? "这个问题还需要定位卡点，因此本轮没有提前检索记忆。"
              : "开始讲解后，这里会说明哪些记忆真正改变了本次路径。"}
          </p>
        )}

        {plan.length > 0 && (
          <details className="execution-trace">
            <summary>
              <span>精简执行轨迹</span>
              <span className="trace-count">{plan.length} 步</span>
              <ChevronDown className="summary-chevron" aria-hidden="true" size={16} />
            </summary>
            <ol>
              {plan.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </details>
        )}
      </section>

      <section className="rail-panel metrics-panel glass-surface" aria-labelledby="metrics-heading">
        <header className="rail-panel-header metrics-header">
          <span className="rail-icon" aria-hidden="true">
            <Gauge size={17} strokeWidth={1.8} />
          </span>
          <div>
            <h2 id="metrics-heading">本轮性能</h2>
          </div>
        </header>

        <div className="service-model" aria-label="当前模型服务">
          <div>
            <span>模型服务</span>
            <strong>{serviceMeta ? formatServiceProvider(serviceMeta.provider) : "读取中"}</strong>
          </div>
          <div>
            <span>当前模型</span>
            <strong>{serviceMeta?.model ?? "—"}</strong>
          </div>
          {serviceMeta?.thinkingEnabled && (
            <span className="thinking-label">
              Thinking · {serviceMeta.reasoningEffort?.toUpperCase() ?? "ON"}
            </span>
          )}
          {serviceMeta?.provider === "template" && (
            <span className="fallback-label">当前为模板降级</span>
          )}
        </div>

        <dl className="metrics-grid">
          {metricItems(presentation?.metrics).map((metric) => {
            const Icon = metric.icon;
            return (
              <div className="metric" key={metric.label}>
                <dt>
                  <Icon aria-hidden="true" size={15} strokeWidth={1.8} />
                  {metric.label}
                </dt>
                <dd>{metric.value}</dd>
              </div>
            );
          })}
        </dl>

        <p className="metrics-note">
          {personalizationIncrement === null
            ? "完成一次讲解后显示真实 token 与延迟。"
            : `本地个性化额外耗时 ${personalizationIncrement} ms；总输入 ${presentation?.metrics.totalInputTokens} tokens。`}
        </p>
        {demoMode && <span className="demo-label">演示数据</span>}
      </section>
    </div>
  );
}
