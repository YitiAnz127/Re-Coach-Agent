// 主题：颜色 + 各 pi-tui 组件 theme（参考 arex theme.ts 的思路）
import chalk from "chalk";
import type { EditorTheme } from "@earendil-works/pi-tui";
import type { MarkdownTheme, SelectListTheme } from "@earendil-works/pi-tui";

export interface ReCoachTheme {
  accent: (s: string) => string;
  userBg: (s: string) => string;
  userText: (s: string) => string;
  assistantText: (s: string) => string;
  thinking: (s: string) => string;
  dim: (s: string) => string;
  border: (s: string) => string;
  error: (s: string) => string;
  success: (s: string) => string;
  bold: (s: string) => string;
  italic: (s: string) => string;
  code: (s: string) => string;
  codeBlock: (s: string) => string;
  quote: (s: string) => string;
  link: (s: string) => string;
  listBullet: (s: string) => string;
  spinner: (s: string) => string;
  loaderMsg: (s: string) => string;
  personalization: (s: string) => string;
  meta: (s: string) => string;
  header: (s: string) => string;
}

export const theme: ReCoachTheme = {
  accent: (s) => chalk.hex("#4aa8ff")(s),
  userBg: (s) => chalk.bgHex("#233047")(s),
  userText: (s) => chalk.hex("#e8eef7")(s),
  assistantText: (s) => chalk.hex("#e8eef7")(s),
  thinking: (s) => chalk.hex("#8b96a8").italic(s),
  dim: (s) => chalk.hex("#6b7686")(s),
  border: (s) => chalk.hex("#3a4a63")(s),
  error: (s) => chalk.hex("#ff6b6b")(s),
  success: (s) => chalk.hex("#7be0a3")(s),
  bold: (s) => chalk.bold(s),
  italic: (s) => chalk.italic(s),
  code: (s) => chalk.hex("#ffd479")(s),
  codeBlock: (s) => chalk.hex("#d4dbe6")(s),
  quote: (s) => chalk.hex("#9aa7b8").italic(s),
  link: (s) => chalk.hex("#4aa8ff").underline(s),
  listBullet: (s) => chalk.hex("#4aa8ff")(s),
  spinner: (s) => chalk.hex("#4aa8ff")(s),
  loaderMsg: (s) => chalk.hex("#8b96a8")(s),
  personalization: (s) => chalk.hex("#c9a86b")(s),
  meta: (s) => chalk.hex("#6b7686")(s),
  header: (s) => chalk.hex("#4aa8ff").bold(s),
};

export function markdownTheme(): MarkdownTheme {
  return {
    heading: (s) => chalk.bold.hex("#4aa8ff")(s),
    link: (s) => chalk.hex("#4aa8ff").underline(s),
    linkUrl: (s) => chalk.hex("#6b7686").underline(s),
    code: (s) => chalk.hex("#ffd479")(s),
    codeBlock: (s) => chalk.hex("#d4dbe6")(s),
    codeBlockBorder: (s) => chalk.hex("#3a4a63")(s),
    quote: (s) => chalk.hex("#9aa7b8").italic(s),
    quoteBorder: (s) => chalk.hex("#3a4a63")(s),
    hr: (s) => chalk.hex("#3a4a63")(s),
    listBullet: (s) => chalk.hex("#4aa8ff")(s),
    bold: (s) => chalk.bold(s),
    italic: (s) => chalk.italic(s),
    strikethrough: (s) => chalk.strikethrough(s),
    underline: (s) => chalk.underline(s),
  };
}

export function selectListTheme(): SelectListTheme {
  return {
    selectedPrefix: (s) => chalk.hex("#4aa8ff").bold(s),
    selectedText: (s) => chalk.hex("#4aa8ff").bold(s),
    description: (s) => chalk.hex("#8b96a8")(s),
    scrollInfo: (s) => chalk.hex("#6b7686")(s),
    noMatch: (s) => chalk.hex("#ff6b6b")(s),
  };
}

export function editorTheme(): EditorTheme {
  return {
    borderColor: (s) => chalk.hex("#3a4a63")(s),
    selectList: selectListTheme(),
  };
}
