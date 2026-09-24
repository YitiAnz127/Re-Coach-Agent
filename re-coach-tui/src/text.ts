/**
 * 与 Python `len()` 一致的字符串长度（按**码点**计数）。
 *
 * 为什么需要单独一个函数：JS 的 `String.prototype.length` 数的是 UTF-16 **码元**，
 * 而 Python 的 `len()` 数的是**码点**。BMP 之外的字（emoji、CJK 扩展 B 区等）
 * 在 JS 里算 2、在 Python 里算 1——于是同一段文本在两侧会跨过不同的阈值。
 *
 * 真实分歧（gate 的澄清阈值，"解释" + 8 个 emoji）：
 *   后端 len(compact) = 10 → 命中 `<= 14` 分支，先问一个澄清问题
 *   TUI  compact.length = 18 → 不命中，直接给出整篇泛泛讲解
 * 也就是"该不该先澄清"这个行为在两端不一致。
 *
 * 中文与英文都在 BMP 内，两种计数完全相同，所以这个改动对所有常规输入
 * 都是零行为变化，只在含 astral 字符时把行为拉回与后端一致。
 */
export function codePointLength(text: string): number {
  let count = 0;
  // 字符串迭代器按码点遍历（不是按码元），且不产生中间数组。
  for (const _char of text) count += 1;
  return count;
}
