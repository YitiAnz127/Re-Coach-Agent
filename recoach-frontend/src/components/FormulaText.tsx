import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/**
 * 消息正文渲染：Markdown + LaTeX 公式（$...$ 行内、$$...$$ 块级）。
 *
 * 流式文本每次按当前内容重新解析；未完成或无效公式不会中断消息。
 * 保持默认 HTML 过滤与 KaTeX trust=false，代码块不参与数学解析。
 * 独立容器隔离正文的 pre-wrap；宽公式在自身区域横向滚动。
 */

export function FormulaText({ content }: { content: string }) {
  const rendered = useMemo(
    () => (
      <div className="formula-text">
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{
            pre: ({ children }) => <pre className="md-pre">{children}</pre>,
            code: ({ className, children }) => (
              <code className={className}>{children}</code>
            ),
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    ),
    [content],
  );
  return rendered;
}
