import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/**
 * 消息正文渲染：Markdown + LaTeX 公式（$...$ 行内、$$...$$ 块级）。
 *
 * - remark-math 天然容错：流式过程中未闭合的 $... 会按纯文本显示，
 *   闭合后自动变成公式，不会打断流式输出。
 * - react-markdown 默认不渲染原始 HTML（无 rehype-raw），KaTeX 输出
 *   由库内部转义，不存在 XSS 注入面。
 * - 代码块用 <pre><code> 结构，方便 CSS 定制。
 */

export function FormulaText({ content }: { content: string }) {
  const rendered = useMemo(
    () => (
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // 代码块带语言类名，便于样式；公式块用 div 包裹以支持换行
          pre: ({ children }) => <pre className="md-pre">{children}</pre>,
          code: ({ className, children }) => (
            <code className={className}>{children}</code>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    ),
    [content],
  );
  return rendered;
}
