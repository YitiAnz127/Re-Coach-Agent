// 轻量 token 估算（与后端 tokens.py 语义一致：中文按字，英文按词）
export function estimateTokens(text: string): number {
  if (!text) return 0;
  // 中文字符
  const cjk = text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g)?.length ?? 0;
  // 去除中文字符后按空白切分英文词
  const nonCjk = text.replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, " ");
  const words = nonCjk.split(/\s+/).filter((w) => w.length > 0).length;
  return cjk + words;
}
