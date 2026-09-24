// 五级作用域记忆（与后端 memory.py 1:1 对齐，持久化通过 Store 抽象）
import type { Memory, MemoryScope, ResolvedTask } from "../types.js";
import { estimateTokens } from "../tokens.js";
import { newMemoryId } from "../ids.js";
import { codePointLength } from "../text.js";

export const EVIDENCE_STRENGTH: Record<string, number> = {
  user_explicit_longterm: 1.0,
  user_explicit_correction: 0.85,
  repeated_independent_feedback: 0.7,
  user_endorsed_explanation: 0.55,
  single_implicit_signal: 0.3,
};

export const W_SCOPE = 0.4;
export const W_LEXICAL = 0.25;
export const W_EVIDENCE = 0.15;
export const W_CONFIDENCE = 0.1;
export const W_RECENCY = 0.1;

const SCOPE_FIELDS: Array<keyof MemoryScope> = [
  "domain",
  "conceptScope",
  "propositionScope",
  "taskScope",
];

/**
 * 参与打分的候选上限（对齐后端 memory.py 的 MEMORY_CANDIDATE_LIMIT）。
 *
 * 记忆条数由用户反馈轮次决定，可无界增长；全量打分会让单轮成本随历史线性上升。
 * 召回本来只选 1~3 条，截断到最近更新的若干条即可，代价可忽略。
 */
export const MEMORY_CANDIDATE_LIMIT = 500;

export interface MemoryStore {
  queryAllActiveMemories(userId: string): Memory[];
  listMemories(userId: string, opts?: { status?: string; type?: string; domain?: string }): Memory[];
  getMemory(id: string): Memory | undefined;
  insertMemory(m: Memory): void;
  updateMemory(m: Memory): void;
}

function now(): number {
  return Date.now();
}

function specificity(memory: Memory): number {
  return SCOPE_FIELDS.filter((f) => memory[f] !== "*" && memory[f] !== "").length;
}

export function scopeMatch(memory: Memory, task: ResolvedTask): number {
  const checks: Record<string, string> = {
    domain: task.domain,
    conceptScope: task.concept,
    propositionScope: task.proposition,
    taskScope: task.taskScope,
  };
  let matched = 0.0;
  for (const f of SCOPE_FIELDS) {
    const scopeValue = memory[f];
    const value = checks[f] ?? "";
    if (scopeValue === "*" || scopeValue === "") {
      matched += 0.5;
      continue;
    }
    if (!value) return 0.0;
    if (f === "propositionScope") {
      if (scopeValue === value || (value.includes(scopeValue) && scopeValue.length >= 4)) {
        matched += 1.0;
      } else {
        return 0.0;
      }
    } else if (scopeValue === value) {
      matched += 1.0;
    } else {
      return 0.0;
    }
  }
  return matched / SCOPE_FIELDS.length;
}

function recency(updatedAt: number): number {
  const ageDays = Math.max(0, (now() - updatedAt) / 86_400_000);
  return Math.max(0, 1 - ageDays / 90);
}

const CJK_RUN_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+/g;
const WORD_RE = /[A-Za-z0-9_]+/g;

export function lexicalTokens(text: string): string[] {
  const tokens: string[] = [];
  for (const m of text.toLowerCase().matchAll(WORD_RE)) {
    tokens.push(m[0]);
  }
  for (const m of text.matchAll(CJK_RUN_RE)) {
    const run = m[0];
    if (run.length === 1) {
      tokens.push(run);
    } else {
      for (let i = 0; i < run.length - 1; i++) {
        tokens.push(run.slice(i, i + 2));
      }
    }
  }
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of tokens) {
    if (t.length >= 2 && !seen.has(t)) {
      seen.add(t);
      out.push(t);
    }
  }
  return out.slice(0, 12);
}

// 内存版 FTS 候选：对 rule 做包含匹配（等价于 LIKE），逐 token 打分
function ftsCandidates(memories: Memory[], text: string): Map<string, number> {
  const tokens = lexicalTokens(text);
  if (tokens.length === 0) return new Map();
  const scores = new Map<string, number>();
  const all = memories;
  for (const token of tokens) {
    for (const m of all) {
      if (m.rule.includes(token)) {
        scores.set(m.id, (scores.get(m.id) ?? 0) + 1 / tokens.length);
      }
    }
  }
  return scores;
}

export interface RetrievalResult {
  selected: Memory[];
  recalledIds: string[];
  searchMs: number;
}

export interface RetrieveArgs {
  memoryOn?: boolean;
  memoryIds?: Set<string>;
  memories?: Memory[];
  memoryMaxSelected: number;
  memoryHardLimit: number;
  /** 候选上限，默认 MEMORY_CANDIDATE_LIMIT；仅供测试收紧。 */
  candidateLimit?: number;
}

export function retrieve(
  store: MemoryStore,
  userId: string,
  task: ResolvedTask,
  args: RetrieveArgs,
): RetrievalResult {
  const started = performance.now();
  const result: RetrievalResult = { selected: [], recalledIds: [], searchMs: 0 };
  const effectiveMemoryOn = args.memoryOn ?? true;
  if (!effectiveMemoryOn) return result;

  // fork 快照是冻结基线，整份参与（与后端一致：快照路径不截断）；
  // 非快照路径按最近更新排序后截断——顺序与后端 ORDER BY updated_at DESC 对齐，
  // 否则同分候选的稳定排序结果会与后端不同。
  let memories = (
    args.memories ??
    store
      .queryAllActiveMemories(userId)
      .slice()
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, args.candidateLimit ?? MEMORY_CANDIDATE_LIMIT)
  ).filter((m) => m.userId === userId && m.status === "active");
  if (args.memoryIds) {
    memories = memories.filter((m) => args.memoryIds!.has(m.id));
  }
  if (memories.length === 0) {
    result.searchMs = Math.round(performance.now() - started);
    return result;
  }

  const queryText = [task.concept, task.proposition, task.goal, task.taskScope]
    .filter(Boolean)
    .join(" ")
    .trim();
  const lexical = ftsCandidates(memories, queryText);

  const scored: Array<[number, Memory]> = [];
  for (const m of memories) {
    const scope = scopeMatch(m, task);
    if (scope <= 0.4 && specificity(m) > 0) continue;
    const score =
      W_SCOPE * scope +
      W_LEXICAL * (lexical.get(m.id) ?? 0) +
      W_EVIDENCE * (EVIDENCE_STRENGTH[m.evidenceKind] ?? 0.3) +
      W_CONFIDENCE * m.confidence +
      W_RECENCY * recency(m.updatedAt);
    scored.push([score, m]);
  }

  scored.sort((a, b) => b[0] - a[0]);
  result.recalledIds = scored.map(([, m]) => m.id);

  const seenKeys = new Set<string>();
  const hasSpecific = scored.some(([, m]) => specificity(m) > 0);
  for (const [score, m] of scored) {
    if (result.selected.length >= args.memoryHardLimit) break;
    if (specificity(m) === 0 && hasSpecific && score < 0.5) continue;
    const key = [m.type, m.domain, m.conceptScope, m.propositionScope, m.taskScope].join("\u0000");
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    result.selected.push(m);
    if (result.selected.length >= args.memoryMaxSelected) break;
  }

  result.searchMs = Math.round(performance.now() - started);
  return result;
}

export function scopeLabel(m: Memory): string {
  return [m.domain, m.conceptScope, m.propositionScope, m.taskScope]
    .map((p) => p || "*")
    .join(" / ");
}

// ---------- 写入、归档、遗忘 ----------

function ruleSimilar(a: string, b: string): boolean {
  const stop = new Set("的了呢吧啊哦嗯请帮我希望以后每次都不再要".split(""));
  const chars = (s: string): Set<string> =>
    new Set(
      Array.from(s).filter(
        (c) => /[\p{L}\p{N}]/u.test(c) && !stop.has(c),
      ),
    );
  const sa = chars(a);
  const sb = chars(b);
  if (sa.size === 0 || sb.size === 0) return false;
  let overlap = 0;
  for (const c of sa) if (sb.has(c)) overlap++;
  return overlap / Math.min(sa.size, sb.size) >= 0.5;
}

export interface WriteMemoryArgs {
  type: Memory["type"];
  rule: string;
  domain?: string;
  conceptScope?: string;
  propositionScope?: string;
  taskScope?: string;
  polarity?: Memory["polarity"];
  evidenceKind?: string;
  confidence?: number;
  sourceEventId: string;
}

export function writeMemory(
  store: MemoryStore,
  userId: string,
  args: WriteMemoryArgs,
): Memory {
  const type = args.type;
  const rule = args.rule;
  const domain = args.domain ?? "*";
  const conceptScope = args.conceptScope ?? "*";
  const propositionScope = args.propositionScope ?? "*";
  const taskScope = args.taskScope ?? "*";
  const polarity = args.polarity ?? "positive";
  const evidenceKind = args.evidenceKind ?? "user_explicit_longterm";
  const confidence = args.confidence ?? 0.8;

  const existingSource = store
    .listMemories(userId)
    .find((m) => m.sourceEventIds.includes(args.sourceEventId));
  if (existingSource) return existingSource;

  const t = now();
  const existing = store
    .listMemories(userId, { status: "active", domain: domain !== "*" ? domain : undefined })
    .filter(
      (m) =>
        m.type === type &&
        m.domain === domain &&
        m.conceptScope === conceptScope &&
        m.propositionScope === propositionScope &&
        m.taskScope === taskScope &&
        ruleSimilar(m.rule, rule),
    );

  const memoryId = newMemoryId();
  for (const old of existing) {
    store.updateMemory({ ...old, status: "archived", supersededBy: memoryId, updatedAt: t });
  }
  const memory: Memory = {
    id: memoryId,
    userId,
    type,
    rule,
    domain,
    conceptScope,
    propositionScope,
    taskScope,
    polarity,
    evidenceKind,
    sourceEventIds: [args.sourceEventId],
    confidence,
    status: "active",
    createdAt: t,
    updatedAt: t,
  };
  store.insertMemory(memory);
  return memory;
}

/**
 * 自然语言遗忘的最小关键字长度（对齐后端 memory.py 的 MIN_FORGET_KEYWORD_LEN）。
 *
 * 再短就会命中共用字（"你"/"我"/"的"），一次误解析即不可逆归档大量无关记忆：
 * 「忘记关于你的规则」解析出的关键字是「你」，会把所有含「你」的记忆一并 archived。
 */
export const MIN_FORGET_KEYWORD_LEN = 2;

export function forgetMemories(
  store: MemoryStore,
  userId: string,
  keyword: string,
  sourceEventId: string,
): string[] {
  keyword = keyword.trim();
  // 未能解析出遗忘对象时必须安全无操作，不能把空字符串解释为“全部记忆”。
  if (!keyword) return [];
  // 关键字过短时 substring 匹配会退化，直接判定“没有找到”（上层据此回报），
  // 不做任何归档——status 翻转不可逆。见 MIN_FORGET_KEYWORD_LEN。
  if (codePointLength(keyword) < MIN_FORGET_KEYWORD_LEN) return [];

  const previous = store
    .listMemories(userId, { status: "forgotten" })
    .filter((m) => m.sourceEventIds.includes(sourceEventId));
  if (previous.length > 0) return previous.map((m) => m.id);

  const forgotten: string[] = [];
  const t = now();
  for (const m of store.listMemories(userId, { status: "active" })) {
    if (keyword && !m.rule.includes(keyword)) continue;
    const sourceIds = Array.from(new Set([...m.sourceEventIds, sourceEventId]));
    store.updateMemory({ ...m, status: "forgotten", sourceEventIds: sourceIds, updatedAt: t });
    forgotten.push(m.id);
  }
  return forgotten;
}

// ---------- 第一级反馈门控 ----------

const LONGTERM_HINT = /(以后|每次|记住|不要再|别再|一直|总是|都按|都先|我喜欢|我偏好|我习惯|保持|继续用|继续这样)/;
const FORGET_HINT = /(忘记|忘掉|遗忘|不用记住|别记住|不要记住|清除|删除|取消)/;
const SESSION_ONLY_HINT = /(这次|本次|这轮|今天|先这样|暂时|就这一次|仅此一次)/;
const NEGATIVE_HINT = /(不要|别|不再|禁止|避免|不喜欢|不习惯|讨厌|反对)/;

export interface FeedbackAction {
  kind: "write_longterm" | "session_only" | "forget" | "none";
  rule: string;
  keyword: string;
  polarity: Memory["polarity"];
  memoryType: Memory["type"];
}

export function classifyFeedback(text: string): FeedbackAction {
  const compact = text.trim();
  const forget = FORGET_HINT.test(compact);
  if (forget) {
    const m = compact.match(/关于(.+?)(?:的)?(?:偏好|记忆|规则|讲法)[。！？!?.]?$/);
    let keyword = m ? m[1] ?? "" : "";
    // 对齐后端：先剥前导「关于与和」，再两侧剥「的/了/空格」。
    // 只剥尾侧会让「忘记关于 的梯度 的偏好」解析出带前导空格的「 的梯度」，
    // 后续 includes 匹配不上任何记忆，遗忘静默失效。
    keyword = keyword
      .replace(/^[关于与和]+/, "")
      .replace(/^[的了 ]+/, "")
      .replace(/[的了 ]+$/, "");
    return { kind: "forget", rule: "", keyword, polarity: "positive", memoryType: "explanation_preference" };
  }
  if (SESSION_ONLY_HINT.test(compact) && !LONGTERM_HINT.test(compact)) {
    return { kind: "session_only", rule: compact, keyword: "", polarity: "positive", memoryType: "explanation_preference" };
  }
  if (LONGTERM_HINT.test(compact)) {
    const polarity: Memory["polarity"] = NEGATIVE_HINT.test(compact) ? "negative" : "positive";
    const memoryType: Memory["type"] = /(每次|以后|一直|总是|都按|都先)/.test(compact)
      ? "interaction_rule"
      : "explanation_preference";
    return { kind: "write_longterm", rule: compact, keyword: "", polarity, memoryType };
  }
  return { kind: "none", rule: "", keyword: "", polarity: "positive", memoryType: "explanation_preference" };
}

export function capsuleOf(memories: Memory[]): { text: string; tokens: number } {
  const lines = memories.map((m) => {
    const prefix = m.polarity === "negative" ? "避免" : "偏好";
    return `- [${m.type}/${scopeLabel(m)}] ${prefix}：${m.rule}`;
  });
  const text = lines.join("\n");
  return { text, tokens: estimateTokens(text) };
}
