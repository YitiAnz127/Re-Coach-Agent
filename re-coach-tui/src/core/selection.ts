// 澄清轮选项的「打字选择」识别（与后端 app/services/selection.py 语义一致）。
//
// 背景：澄清轮把选项渲染成 A/B/C/D/E，但用户很自然地直接打字回「1」或「A」。
// 不识别的话会被当成全新问题，又抛出一整篇泛泛的讲解。
//
// 刻意保守：只有上一轮**确实是带选项的澄清轮**时才生效，
// 避免把正常短消息误当成选项编号。
import type { ClarificationOption, TurnPresentation } from "../types.js";
import { codePointLength } from "../text.js";

// 允许「1」「A」「1.」「A、」「第2个」「选C」等常见写法
const PATTERNS: RegExp[] = [
  /^(?:第)?\s*([1-9])\s*(?:个|项|条)?$/,
  /^(?:第)?\s*([A-Za-z])\s*(?:个|项|条)?$/,
  /^(?:选|选择)\s*([1-9A-Za-z])$/,
];

/** 把选择符解析成 0 基下标；不是选择符时返回 null。 */
function indexFromSelector(raw: string): number | null {
  const text = raw.trim().replace(/^[。．.、,，)）\]】\s]+|[。．.、,，)）\]】\s]+$/g, "");
  // 与后端 len(text) > 8 对齐（码点，不是 UTF-16 码元）
  if (!text || codePointLength(text) > 8) return null;
  for (const pattern of PATTERNS) {
    const match = pattern.exec(text);
    const token = match?.[1];
    if (token === undefined) continue;
    if (/^\d$/.test(token)) {
      const value = Number(token);
      return value >= 1 && value <= 9 ? value - 1 : null;
    }
    // 字母：A/a -> 0，B/b -> 1 ...
    return token.toUpperCase().charCodeAt(0) - "A".charCodeAt(0);
  }
  return null;
}

/** 从上一轮的 presentation 中取出澄清选项；没有则返回空数组。 */
export function parseOptions(presentation: TurnPresentation | undefined): ClarificationOption[] {
  if (!presentation || presentation.mode !== "clarify") return [];
  const options = presentation.clarificationOptions;
  return Array.isArray(options) ? options : [];
}

/**
 * 把选择符解析成对应选项的 followUp。
 * 无法判定时返回 null，调用方应保持原输入不变。
 */
export function resolveOptionSelection(
  userText: string,
  options: ClarificationOption[],
): string | null {
  if (options.length === 0) return null;

  // 1) 直接打出了选项文案本身
  const normalized = userText.trim().replace(/^[。．.、,，\s]+|[。．.、,，\s]+$/g, "");
  for (const option of options) {
    if (option.label && normalized === option.label.trim() && option.followUp.trim()) {
      return option.followUp;
    }
  }

  // 2) 编号或字母
  const index = indexFromSelector(userText);
  if (index === null || index < 0 || index >= options.length) return null;
  const followUp = options[index]?.followUp?.trim();
  return followUp || null;
}
