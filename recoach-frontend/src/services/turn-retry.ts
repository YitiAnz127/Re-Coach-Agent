import type { ChatMessage } from "../types";

export interface PreparedAssistantRetry {
  messages: ChatMessage[];
  assistantId: string;
  content: string;
  clientTurnId: string;
}

export function prepareAssistantRetry(
  messages: ChatMessage[],
  assistantId: string,
): PreparedAssistantRetry {
  const target = messages.find((message) => message.id === assistantId);
  if (!target || target.role !== "assistant" || target.state !== "error") {
    throw new Error("没有找到可重试的回答。");
  }
  if (target.retryable === false) {
    throw new Error("该错误不可重试。");
  }
  if (!target.clientTurnId || !target.requestContent) {
    throw new Error("缺少重试所需的逻辑回合信息。");
  }

  return {
    assistantId,
    content: target.requestContent,
    clientTurnId: target.clientTurnId,
    messages: messages.map((message) =>
      message.id === assistantId
        ? {
            ...message,
            content: "",
            state: "streaming",
            presentation: undefined,
            errorCode: undefined,
            thinking: undefined,
            requestId: undefined,
            turnId: undefined,
            elapsedMs: undefined,
            completedAt: undefined,
          }
        : message,
    ),
  };
}

