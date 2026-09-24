/**
 * 2026-09-17 安全审查加固项的回归测试。
 *
 * 覆盖：
 * - 终端控制序列注入（助手正文来自可配置 LLM 端点，属不可信输入）
 * - 明文 http 端点导致 API Key 泄露
 * - store.json 权限
 */
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { sanitizeTerminalText } from "../src/ui/sanitize.js";
import { checkBaseUrlSecurity, loadConfig } from "../src/config.js";
import { Store } from "../src/store.js";

const ESC = String.fromCharCode(27);
const BEL = String.fromCharCode(7);
const BS = String.fromCharCode(8);
const VT = String.fromCharCode(11);
const C1 = String.fromCharCode(0x9b);
const ST = ESC + "\\";

function hasControlChars(value: string): boolean {
  return [...value].some((ch) => {
    const n = ch.codePointAt(0) ?? 0;
    return (n < 32 && n !== 9 && n !== 10) || (n >= 127 && n <= 159);
  });
}

describe("sanitizeTerminalText", () => {
  it("剥掉改窗口标题的 OSC 2 序列", () => {
    const out = sanitizeTerminalText(`${ESC}]2;pwned${BEL}正常文本`);
    expect(out).toBe("正常文本");
  });

  it("剥掉写剪贴板的 OSC 52 序列", () => {
    const out = sanitizeTerminalText(`${ESC}]52;c;c2VjcmV0${BEL}正文`);
    expect(out).toBe("正文");
  });

  it("剥掉用于伪造界面的光标定位序列", () => {
    // 攻击者曾可覆盖聊天区，伪造"会话过期，请输入 API Key"
    const out = sanitizeTerminalText(`${ESC}[2J${ESC}[H会话已过期，请输入 API Key:`);
    expect(hasControlChars(out)).toBe(false);
    expect(out.includes(ESC)).toBe(false);
    // 纯文本残留是允许的：模型仍能打印吓人文字，但无法控制界面
    expect(out).toContain("会话已过期");
  });

  it("剥掉 OSC 8 超链接", () => {
    const out = sanitizeTerminalText(
      `${ESC}]8;;http://evil.example${ST}点我${ESC}]8;;${ST}`,
    );
    expect(out).toBe("点我");
  });

  it("剥掉 SGR 颜色序列（防止覆盖 chalk 样式）", () => {
    expect(sanitizeTerminalText(`${ESC}[31m红色${ESC}[0m`)).toBe("红色");
  });

  it("剥掉裸控制字节：退格 / BEL / VT", () => {
    expect(sanitizeTerminalText(`安全${BS}${BS}危险`)).toBe("安全危险");
    expect(sanitizeTerminalText(`a${BEL}b`)).toBe("ab");
    expect(sanitizeTerminalText(`a${VT}b`)).toBe("ab");
  });

  it("剥掉 C1 区控制字符（0x9B）", () => {
    const out = sanitizeTerminalText(`${C1}31mC1注入`);
    expect(hasControlChars(out)).toBe(false);
  });

  it("保留换行、制表与中文，不破坏 Markdown", () => {
    const keep = "第一行\n\n- 列表\t制表 **粗体** `code` 中文";
    expect(sanitizeTerminalText(keep)).toBe(keep);
  });

  it("空输入安全", () => {
    expect(sanitizeTerminalText("")).toBe("");
  });
});

describe("checkBaseUrlSecurity", () => {
  it("https 端点放行", () => {
    expect(checkBaseUrlSecurity("https://api.example.com/v1", true)).toBeNull();
  });

  it("明文 http 携带 Key 时拒绝", () => {
    const problem = checkBaseUrlSecurity("http://api.example.com/v1", true);
    expect(problem).not.toBeNull();
    expect(problem).toContain("https");
  });

  it("本机 http 放行（本地推理服务常见）", () => {
    expect(checkBaseUrlSecurity("http://localhost:11434/v1", true)).toBeNull();
    expect(checkBaseUrlSecurity("http://127.0.0.1:8000/v1", true)).toBeNull();
  });

  it("未配置 Key 时不阻断", () => {
    expect(checkBaseUrlSecurity("http://api.example.com/v1", false)).toBeNull();
  });

  it("非法 URL 报错", () => {
    expect(checkBaseUrlSecurity("not a url", true)).not.toBeNull();
  });
});

describe("store 文件权限", () => {
  it("store.json 与数据目录不是全局可读", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-perm-"));
    const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "template" });
    const store = new Store(cfg);
    store.createSession();

    const storeFile = path.join(dir, "store.json");
    expect(fs.existsSync(storeFile)).toBe(true);

    // Windows 上 mode 位不完整生效，只在 POSIX 上断言
    if (process.platform !== "win32") {
      const fileMode = fs.statSync(storeFile).mode & 0o777;
      expect(fileMode & 0o077).toBe(0); // 组/其他用户无任何权限
    }
    expect(JSON.parse(fs.readFileSync(storeFile, "utf8"))).toBeTruthy();
  });
});

describe("store 元素级形状校验", () => {
  it("被篡改的元素不会再导致启动崩溃", () => {
    // 回归：构造函数原本只校验顶层是 5 个数组，元素形状完全信任。
    // 手工改坏的 store.json（rule 不是字符串）能通过顶层校验，
    // 然后在 m.rule.includes(...) 处抛异常，形成"一启动就崩"的循环。
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-tamper-"));
    const storeFile = path.join(dir, "store.json");
    fs.writeFileSync(
      storeFile,
      JSON.stringify({
        memories: [
          { id: "m1", userId: "u1", rule: 123, status: "active", confidence: "high" },
          { id: "m2", userId: "u1", rule: { nested: true }, sourceEventIds: "not-an-array" },
          null,
        ],
        conceptStates: [null, { id: "c1", concept: 42 }],
        sessions: [null],
        messages: [null, { id: "msg1", content: 999 }],
        events: [null, { id: "e1", kind: 5, payload: "not-an-object" }],
      }),
      "utf8",
    );

    const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "template" });
    // 不得抛异常
    const store = new Store(cfg);
    const memories = store.queryAllActiveMemories("u1");
    // 非字符串 rule 被修正为空串而不是让后续 .includes/.slice 崩掉
    for (const m of memories) {
      expect(typeof m.rule).toBe("string");
      expect(Array.isArray(m.sourceEventIds)).toBe(true);
      expect(typeof m.confidence).toBe("number");
    }
  });

  it("仍然拒绝完全不是对象或缺少顶层数组的文件", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "recoach-badfile-"));
    fs.writeFileSync(path.join(dir, "store.json"), JSON.stringify({ memories: [] }), "utf8");
    const cfg = loadConfig({ RECOACH_DATA_DIR: dir, RECOACH_LLM_PROVIDER: "template" });
    expect(() => new Store(cfg)).toThrow(/Invalid store format/);
  });
});
