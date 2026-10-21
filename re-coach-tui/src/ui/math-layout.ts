import { renderLatex, visibleWidth, type MarkdownOptions } from "@earendil-works/pi-tui";

function closingAt(line: string, delimiter: string, from = 0): number {
  let at = line.indexOf(delimiter, from);
  while (at >= 0) {
    let escapes = 0;
    for (let p = at - 1; p >= 0 && line[p] === "\\"; p--) escapes++;
    if (escapes % 2 === 0) return at;
    at = line.indexOf(delimiter, at + delimiter.length);
  }
  return -1;
}

function codeSpan(text: string): string {
  // Protect rendered underscores/asterisks from being parsed as Markdown emphasis.
  const longest = Math.max(0, ...[...text.matchAll(/`+/g)].map(m => m[0].length));
  const fence = "`".repeat(longest + 1);
  return `${fence} ${text} ${fence}`;
}

/** Adapt only completed display math that cannot fit the actual terminal width.
 * Native Markdown wraps each drawn fraction row independently; a linear fraction
 * keeps numerator/denominator grouping intact when wrapping is unavoidable.
 * This is display-only: the original answer remains in messages and memory.
 */
export function adaptTerminalMath(source: string, availableWidth: number): string {
  const lines = source.split("\n"), out: string[] = [];
  let codeFence: { marker: string; length: number } | undefined;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    const fenced = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (codeFence) {
      if (fenced && fenced[1]![0] === codeFence.marker && fenced[1]!.length >= codeFence.length
        && line.slice(fenced[0].length).trim() === "") codeFence = undefined;
      out.push(line); continue;
    }
    if (fenced) {
      codeFence = { marker: fenced[1]![0]!, length: fenced[1]!.length };
      out.push(line); continue;
    }
    const opening = /^( {0,3})(\$\$|\\\[)/.exec(line);
    if (!opening) { out.push(line); continue; }
    const delimiter = opening[2] === "$$" ? "$$" : "\\]";
    const parts: string[] = [];
    let endLine = i, closed = false;
    for (; endLine < lines.length; endLine++) {
      const tail = lines[endLine]!.slice(endLine === i ? opening[0].length : 0);
      const close = closingAt(tail, delimiter);
      if (close >= 0) {
        if (tail.slice(close + delimiter.length).trim()) break; // Inline text, leave to native parsing.
        parts.push(tail.slice(0, close)); closed = true; break;
      }
      parts.push(tail);
    }
    if (!closed) { out.push(line); continue; }
    const math = parts.join("\n").trim();
    const stacked = renderLatex(math, { display: true });
    const overflows = stacked !== undefined && stacked.split("\n").some(row => visibleWidth(row) > availableWidth);
    const linear = overflows ? renderLatex(math) : undefined;
    if (linear !== undefined) {
      out.push(...linear.split("\n").map(row => opening[1] + codeSpan(row) + "  "));
    } else {
      out.push(...lines.slice(i, endLine + 1));
    }
    i = endLine;
  }
  return out.join("\n");
}

export const terminalMathOptions: MarkdownOptions = { transform: adaptTerminalMath };
