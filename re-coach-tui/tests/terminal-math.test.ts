import { describe, expect, it } from "vitest";
import { Markdown, visibleWidth } from "@earendil-works/pi-tui";
import { adaptTerminalMath, terminalMathOptions } from "../src/ui/math-layout.js";

const identity = (s: string) => s;
const theme = { heading: identity, link: identity, linkUrl: identity, code: identity,
  codeBlock: identity, codeBlockBorder: identity, quote: identity, quoteBorder: identity,
  hr: identity, listBullet: identity, bold: identity, italic: identity, strikethrough: identity, underline: identity };
const attention = String.raw`$$\mathrm{Attention}(Q,K,V)=\mathrm{softmax}\left(\frac{QK^\top}{\sqrt{d_k}}\right)V$$`;
function render(source: string, width: number) {
  return new Markdown(source, 1, 0, theme, undefined, terminalMathOptions).render(width).map(s => s.trimEnd());
}

describe("terminal formula layout", () => {
  it("does not independently wrap the numerator and denominator of an overwide fraction", () => {
    const lines = render(attention, 36);
    expect(lines.join("\n")).not.toContain("─");
    expect(lines.every(line => visibleWidth(line) <= 36)).toBe(true);
    expect(lines.join(" ")).toContain("softmax");
    expect(lines.join(" ")).toContain("/");
    expect(lines.join(" ")).not.toContain("\\frac");
  });
  it("preserves native stacked layout when the display expression fits", () => {
    expect(adaptTerminalMath(attention, 100)).toBe(attention);
    expect(render(attention, 100).join("\n")).toContain("─");
  });
  it("preserves row order in a wide aligned expression and both math delimiters", () => {
    const source = String.raw`\[
\begin{aligned}
z_i &= \frac{e^{x_i}}{\sum_{j=1}^{n}e^{x_j}} + \frac{e^{x_i}}{\sum_{j=1}^{n}e^{x_j}} \\
y_i &= \sum_{j=1}^{n} a_{ij}v_j
\end{aligned}
\]`;
    const lines = render(source, 24).join("\n");
    expect(lines.indexOf("zᵢ")).toBeLessThan(lines.indexOf("yᵢ"));
    expect(lines).not.toContain("─");
    expect(lines).not.toContain("begin{aligned}");
  });
  it("keeps code examples, incomplete math and ordinary text unchanged", () => {
    const examples = ["```latex\n" + attention + "\n```", "~~~text\n" + attention + "\n~~~",
      String.raw`$$\frac{a}{b}`, "价格 $20，变量 $x_i$。", "`$$x$$`", "```\n" + attention];
    for (const source of examples) expect(adaptTerminalMath(source, 8)).toBe(source);
  });
  it("uses the same layout after streaming completes and during history replay", () => {
    const view = new Markdown("", 1, 0, theme, undefined, terminalMathOptions);
    for (let i = 1; i <= attention.length; i++) view.setText(attention.slice(0, i));
    expect(view.render(36)).toEqual(new Markdown(attention, 1, 0, theme, undefined, terminalMathOptions).render(36));
    expect(view.render(100).join("\n")).toContain("─");
  });
  it("retains unsupported math and does not interpret literal escaped delimiters", () => {
    for (const source of [String.raw`$$\unknowncommand{a}$$`, String.raw`\$$not math`]) {
      expect(adaptTerminalMath(source, 4)).toBe(source);
    }
  });
});
