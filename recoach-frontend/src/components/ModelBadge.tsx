import { AlertTriangle, Sparkles } from "lucide-react";
import { fallbackNotice } from "../services/fallback-notice";
import { formatServiceProvider, isServiceProvider, type ServiceMeta } from "../services/service-meta";
import type { TurnPresentation } from "../types";

interface ModelBadgeProps {
  presentation?: TurnPresentation;
  serviceMeta?: ServiceMeta;
}

/**
 * 顶栏的"当前模型"指示器。
 *
 * 常驻可见，让用户随时知道自己在和真实模型对话还是在内置模板上：
 * - 正常：`DeepSeek · deepseek-flash`
 * - 降级/未启用：带警示色的徽章，悬停给出原因
 *
 * 依据是本轮**实际**provider（presentation.metrics），而不是 /meta 的配置值——
 * 配了密钥但鉴权失败时，配置值仍显示 deepseek，只有实际值才反映真相。
 */
export function ModelBadge({ presentation, serviceMeta }: ModelBadgeProps) {
  if (!serviceMeta) {
    return <span className="model-badge model-badge-loading">读取中…</span>;
  }

  const notice = fallbackNotice(presentation, serviceMeta);
  const reported = presentation?.metrics?.provider;
  const provider = isServiceProvider(reported) ? reported : serviceMeta.provider;
  const model = presentation?.metrics?.model || serviceMeta.model;

  if (notice) {
    return (
      <span className="model-badge model-badge-warn" title={notice.hint} role="status">
        <AlertTriangle aria-hidden="true" size={14} strokeWidth={2} />
        {notice.label}
      </span>
    );
  }

  return (
    <span className="model-badge" title={`当前模型：${model}`}>
      <Sparkles aria-hidden="true" size={14} strokeWidth={1.8} />
      {formatServiceProvider(provider)}
      {model && model !== "template" ? <em>· {model}</em> : null}
    </span>
  );
}
