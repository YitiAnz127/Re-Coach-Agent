/**
 * 终端控制序列净化。
 *
 * 为什么必须有：助手正文来自 `RECOACH_LLM_BASE_URL` 指向的端点，属于**不可信输入**；
 * 而 pi-tui 的渲染路径刻意保留 ANSI（依赖内 text.js 明确注释 "preserves ANSI codes"），
 * 只在复制/选择时才调用 stripTerminalSequences。因此模型返回的裸 ESC 字节会
 * 原样写进主人的终端，可被用来：
 *   - 改窗口标题（OSC 2）、写剪贴板（OSC 52）
 *   - 用光标定位序列覆盖聊天区，伪造"会话过期，请输入 API Key"之类的界面
 *   - OSC 8 超链接钓鱼
 * 在内容进入渲染之前剥掉 C0/C1 控制字符即可，chalk 的着色是在之后施加的，不受影响。
 */

// CSI / OSC / 其它两字节转义序列
const ANSI_SEQUENCE = new RegExp(
  [
    // CSI: ESC [ 参数字节 中间字节 终止字节
    "[\\u001B\\u009B]\\[[0-?]*[ -/]*[@-~]",
    // OSC: ESC ] ... BEL 或 ST
    "[\\u001B\\u009B]\\][^\\u0007\\u001B]*(?:\\u0007|\\u001B\\\\)?",
    // 其它双字节转义
    "[\\u001B\\u009B][@-Z\\\\-_a-z]",
  ].join("|"),
  "g",
);

// 残留的裸控制字符。刻意保留 TAB (U+0009) 与 LF (U+000A)：
// 二者是 Markdown 正文的合法组成，其余 C0/C1 一律剔除。
const LONE_CONTROL = new RegExp("[\\u0000-\\u0008\\u000B-\\u001F\\u007F-\\u009F]", "g");

/** 剥掉终端控制序列。用于一切来自模型/服务端的字符串。 */
export function sanitizeTerminalText(text: string): string {
  if (!text) return "";
  return text.replace(ANSI_SEQUENCE, "").replace(LONE_CONTROL, "");
}
