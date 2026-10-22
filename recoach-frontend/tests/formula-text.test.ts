import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer, type ViteDevServer } from "vite";

let server: ViteDevServer;
let FormulaText: (props: { content: string }) => React.ReactNode;
before(async () => {
  server = await createServer({ server: { middlewareMode: true }, logLevel: "silent" });
  ({ FormulaText } = await server.ssrLoadModule("/src/components/FormulaText.tsx"));
});
after(async () => { await server?.close(); });

function render(content: string) {
  return renderToStaticMarkup(createElement(FormulaText, { content }));
}

test("真正的消息组件渲染行内上下标，而不是显示 LaTeX 原文", () => {
  const html = render(String.raw`权重 $W_Q$ 与 $x_i^2$。`);
  assert.match(html, /class="katex"/);
  assert.match(html, /<msubsup>/);
  assert.doesNotMatch(html, /\$x_i\^2\$/);
});

test("块级分式、矩阵和多行等式由 KaTeX 排版", () => {
  const html = render(String.raw`$$
\begin{aligned}
S &= \frac{QK^\top}{\sqrt{d_k}} \\
A &= \mathrm{softmax}(S)
\end{aligned}
$$

$$
\begin{pmatrix}a&b\\c&d\end{pmatrix}
$$`);
  assert.equal((html.match(/class="katex-display"/g) ?? []).length, 2);
  assert.match(html, /<mfrac>/);
  assert.match(html, /<mtable/);
  assert.doesNotMatch(html, /katex-error/);
});

test("流式未完成公式不崩溃，闭合后解析成公式", () => {
  const content = String.raw`结果 $x_i^2$。`;
  for (let i = 0; i < content.length; i++) assert.doesNotThrow(() => render(content.slice(0, i)));
  assert.match(render(content), /class="katex"/);
});

test("代码中的公式原样保留，坏公式不使整条消息崩溃", () => {
  const code = render('`$x_i$`\n\n```tex\n$$x^2$$\n```');
  assert.doesNotMatch(code, /class="katex"/);
  assert.match(code, /\$x_i\$/);
  const invalid = render(String.raw`前文 $\notARealCommand{x}$ 后文`);
  assert.match(invalid, /前文/);
  assert.match(invalid, /后文/);
  assert.doesNotMatch(render('<script>alert(1)</script>'), /<script>/);
});

test("KaTeX 渲染器与 CSS 使用同一份依赖", () => {
  const require = createRequire(import.meta.url);
  const pluginRequire = createRequire(require.resolve("rehype-katex"));
  assert.equal(pluginRequire.resolve("katex"), require.resolve("katex"));
});
