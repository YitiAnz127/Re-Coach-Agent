// 轻量 token 估算（与后端 app/tokens.py 的 estimate_tokens 逐字对齐）
//
// 公式：CJK 每字算 1，其余字符每 4 个算 1（下限 1）。
//
// 回归：本文件曾把非中文部分按**空白分词**计数，理由是"英文按词"。
// 但后端从来不是这么算的（它用 len(other) // 4），两者在英文文本上最大差到 7：
//   "supercalifragilisticexpialidocious"  后端 8 / 旧实现 1
//   "a b c d e f g h i j"                 后端 4 / 旧实现 10
// 这个值用于 Memory Capsule 的 280 token 预算判定，两侧不一致会让 TUI 放进
// 后端本该裁掉的记忆。文件头的"语义一致"注释当时也是错的——注释不会自己成立。

// 与后端 tokens.py 的 [一-鿿] 一致：只算基本区，不含扩展 A 区。
// 不使用 /g 标志，避免共享 lastIndex 带来的隐式状态。
const CJK_CHAR = /[一-鿿]/;

export function estimateTokens(text: string): number {
  if (!text) return 0;
  // 按码点切分，与 Python 的 len() 一致（BMP 之外的字符在 .length 里算 2）
  const chars = Array.from(text);
  const cjk = chars.filter((ch) => CJK_CHAR.test(ch)).length;
  const other = chars.length - cjk;
  return cjk + Math.max(1, Math.floor(other / 4));
}
