import type { TurnPresentation } from "../types";
import type { ServiceMeta } from "./service-meta";

/** 降级原因的面向用户的说明。未知原因也要显示，只是提示更笼统。 */
const FALLBACK_HINTS: Record<string, string> = {
  AUTH: "模型服务拒绝了访问凭证（密钥无效、已过期或无权限）。",
  QUOTA: "模型服务返回额度不足或频率限制。",
  TIMEOUT: "模型服务响应超时。",
  NETWORK: "无法连接模型服务（网络或 DNS 问题）。",
  PROVIDER_ERROR: "模型服务端错误。",
  HTTP_ERROR: "模型服务返回异常状态。",
  ERROR: "调用模型时出错。",
};

export interface FallbackNotice {
  /** 短标签，用于侧栏徽章 */
  label: string;
  /** 完整说明，用于对话区的提示条 */
  detail: string;
  /** 鼠标悬停时的补充信息 */
  hint: string;
}

/**
 * 判断是否要向用户披露降级。
 *
 * 优先级：本轮实际降级 > 压根没配模型。
 * fallback 字段缺失（旧后端）时不能当成降级，否则会误报。
 *
 * 抽成公共函数是因为它同时被"对话区"和"侧栏"使用——两处各写一份必然会漂移，
 * 而漂移的后果正是本函数要解决的问题：用户看不到降级。
 */
export function fallbackNotice(
  presentation: TurnPresentation | undefined,
  serviceMeta: ServiceMeta | undefined,
): FallbackNotice | null {
  const metrics = presentation?.metrics;
  if (metrics?.fallback) {
    const reason = metrics.fallbackReason ?? "";
    const requested = metrics.model || serviceMeta?.model || "已配置的模型";
    const why = FALLBACK_HINTS[reason] ?? FALLBACK_HINTS.ERROR;
    return {
      label: "本轮为模板降级",
      detail: `${why}本轮回答由模板生成，不是模型输出。`,
      hint: `${why}\n原本请求：${requested}`,
    };
  }
  if (serviceMeta?.provider === "template") {
    // 最常见的配置失误：填了密钥但没改 RECOACH_LLM_PROVIDER，密钥永远不会被使用。
    const unused = Object.entries(serviceMeta.keysPresent ?? {})
      .filter(([, present]) => present)
      .map(([name]) => name);
    if (unused.length > 0) {
      return {
        label: "密钥已配置但未启用",
        detail: `检测到已配置密钥（${unused.join(" / ")}），但 provider 仍为 template，密钥不会被使用。请设置 RECOACH_LLM_PROVIDER=deepseek（或对应 provider）后重启。`,
        hint: "填了密钥但 provider 仍是 template，密钥不会被使用。",
      };
    }
    return {
      label: "当前为模板模式",
      detail: "尚未配置真实模型，回答由模板生成。",
      hint: "尚未配置真实模型，回答由模板生成。",
    };
  }
  return null;
}
