// JSON 文件持久化（独立应用的数据真相来源；替代后端 SQLite）
import * as fs from "node:fs";
import * as path from "node:path";
import type { AppConfig } from "./config.js";
import { newSessionId, newMemoryId, newTurnId } from "./ids.js";
import type {
  ClarificationOption,
  ConceptState,
  LedgerEvent,
  Memory,
  Message,
  SessionBrief,
} from "./types.js";
import { emptyBrief } from "./types.js";
import type { EventStore } from "./core/events.js";
import { classifyFeedback, type MemoryStore } from "./core/memory.js";
import type { BriefStore } from "./core/brief.js";

interface PersistedSession {
  id: string;
  userId: string;
  brief: SessionBrief;
  version: number;
  createdAt: number;
  updatedAt: number;
  isFork: boolean;
  memoryMode?: "on" | "off";
  memorySnapshotIds?: string[];
  memorySnapshot?: Memory[];
  conceptSnapshot?: ConceptState[];
  conceptSnapshotIds?: string[];
  forkGroupId?: string;
}

interface DataFile {
  memories: Memory[];
  conceptStates: ConceptState[];
  sessions: PersistedSession[];
  messages: Message[];
  events: LedgerEvent[];
}

/**
 * 过滤掉非对象元素（如 null / 字符串）。这类元素没有任何可恢复字段，
 * 保留它们只会在后续访问属性时抛异常，直接丢弃是唯一合理选择。
 */
function asRecords<T>(value: unknown): T[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is T => item !== null && typeof item === "object");
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/**
 * 元素级形状校验。
 *
 * 构造函数原本只校验顶层是 5 个数组，元素形状完全信任。被手工改坏的
 * store.json（例如 `"rule": 123`）能通过顶层校验，然后在
 * `m.rule.includes(...)` / `memory.rule.slice(...)` 处抛异常，
 * 形成"一启动就崩"的循环且无法自愈。
 *
 * 这里只做非破坏性修正：把类型不对的字段替换为安全默认值，记录本身保留，
 * 不静默丢弃用户数据。
 */
function normalizeData(data: DataFile): DataFile {
  return {
    memories: asRecords<Memory>(data.memories).map((m) => ({
      ...m,
      id: asString(m.id),
      userId: asString(m.userId),
      type: asString(m.type, "explanation_preference") as Memory["type"],
      rule: asString(m.rule),
      domain: asString(m.domain, "*"),
      conceptScope: asString(m.conceptScope, "*"),
      propositionScope: asString(m.propositionScope, "*"),
      taskScope: asString(m.taskScope, "*"),
      polarity: asString(m.polarity, "positive") as Memory["polarity"],
      evidenceKind: asString(m.evidenceKind, "single_implicit_signal"),
      sourceEventIds: asStringArray(m.sourceEventIds),
      confidence: asNumber(m.confidence, 0.8),
      status: asString(m.status, "active") as Memory["status"],
      createdAt: asNumber(m.createdAt),
      updatedAt: asNumber(m.updatedAt),
    })),
    conceptStates: asRecords<ConceptState>(data.conceptStates).map((s) => ({
      ...s,
      id: asString(s.id),
      userId: asString(s.userId),
      domain: asString(s.domain, "*"),
      concept: asString(s.concept),
      proposition: asString(s.proposition),
      state: asString(s.state),
      evidenceKind: asString(s.evidenceKind),
      sourceEventId: asString(s.sourceEventId),
      createdAt: asNumber(s.createdAt),
      updatedAt: asNumber(s.updatedAt),
    })),
    sessions: asRecords<PersistedSession>(data.sessions).map((s) => ({
      ...s,
      id: asString(s.id),
      userId: asString(s.userId),
      brief: (s.brief && typeof s.brief === "object" ? s.brief : emptyBrief()) as SessionBrief,
      version: asNumber(s.version, 1),
      createdAt: asNumber(s.createdAt),
      updatedAt: asNumber(s.updatedAt),
      isFork: s.isFork === true,
      memorySnapshotIds: asStringArray(s.memorySnapshotIds),
      conceptSnapshotIds: asStringArray(s.conceptSnapshotIds),
    })),
    messages: asRecords<Message>(data.messages).map((m) => ({
      ...m,
      id: asString(m.id),
      sessionId: asString(m.sessionId),
      turnId: asString(m.turnId),
      role: asString(m.role, "user") as Message["role"],
      content: asString(m.content),
      createdAt: asNumber(m.createdAt),
    })),
    events: asRecords<LedgerEvent>(data.events).map((e) => ({
      ...e,
      id: asString(e.id),
      userId: asString(e.userId),
      turnId: asString(e.turnId),
      kind: asString(e.kind, "turn_started") as LedgerEvent["kind"],
      payload:
        e.payload && typeof e.payload === "object"
          ? (e.payload as Record<string, unknown>)
          : {},
      createdAt: asNumber(e.createdAt),
    })),
  };
}

export class Store implements MemoryStore, BriefStore, EventStore {
  private data: DataFile = { memories: [], conceptStates: [], sessions: [], messages: [], events: [] };
  private filePath: string;
  private userId: string;

  constructor(cfg: AppConfig) {
    this.filePath = cfg.storeFile;
    this.userId = cfg.user;
    if (fs.existsSync(this.filePath)) {
      const data = JSON.parse(fs.readFileSync(this.filePath, "utf8"));
      if (!data || typeof data !== "object" ||
          !["memories", "conceptStates", "sessions", "messages", "events"].every(
            (key) => Array.isArray(data[key]),
          )) {
        throw new Error(`Invalid store format: ${this.filePath}`);
      }
      this.data = normalizeData(data as DataFile);
    }
  }

  private save(): void {
    // store.json 含完整对话历史与学习偏好，属于个人数据：
    // 目录 0700、文件 0600，避免同机其他用户可读（Windows 上由 ACL 继承兜底）。
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true, mode: 0o700 });
    const temporaryPath = `${this.filePath}.${newTurnId()}.tmp`;
    try {
      fs.writeFileSync(temporaryPath, JSON.stringify(this.data, null, 2), {
        encoding: "utf8",
        mode: 0o600,
      });
      fs.renameSync(temporaryPath, this.filePath);
    } finally {
      fs.rmSync(temporaryPath, { force: true });
    }
  }

  // ---- MemoryStore ----
  queryAllActiveMemories(userId: string): Memory[] {
    return this.data.memories.filter((m) => m.userId === userId && m.status === "active");
  }
  listMemories(userId: string, opts?: { status?: string; type?: string; domain?: string }): Memory[] {
    return this.data.memories.filter((m) => {
      if (m.userId !== userId) return false;
      if (opts?.status && m.status !== opts.status) return false;
      if (opts?.type && m.type !== opts.type) return false;
      if (opts?.domain && m.domain !== opts.domain && m.domain !== "*") return false;
      return true;
    });
  }
  getMemory(id: string): Memory | undefined {
    return this.data.memories.find((m) => m.id === id);
  }
  insertMemory(m: Memory): void {
    this.data.memories.push(m);
    this.save();
  }
  updateMemory(m: Memory): void {
    const i = this.data.memories.findIndex((x) => x.id === m.id);
    if (i !== -1) {
      this.data.memories[i] = m;
      this.save();
    }
  }

  // ---- BriefStore ----
  listConceptStates(userId: string, concept: string, domain: string, limit: number): ConceptState[] {
    return this.data.conceptStates
      .filter((s) => s.userId === userId && (s.concept === concept || (s.domain === domain && concept === "")))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, limit);
  }
  upsertConceptState(s: ConceptState): string {
    const existing = this.data.conceptStates.find(
      (x) =>
        x.userId === s.userId &&
        x.domain === s.domain &&
        x.concept === s.concept &&
        x.proposition === s.proposition,
    );
    if (existing) {
      existing.state = s.state;
      existing.evidenceKind = s.evidenceKind;
      existing.sourceEventId = s.sourceEventId;
      existing.updatedAt = s.updatedAt;
      this.save();
      return existing.id;
    }
    this.data.conceptStates.push(s);
    this.save();
    return s.id;
  }

  // ---- EventStore ----
  logEvent(e: LedgerEvent): LedgerEvent {
    const existing = this.data.events.find(
      (x) => x.turnId === e.turnId && x.kind === e.kind,
    );
    if (existing) return existing;
    this.data.events.push(e);
    this.save();
    return e;
  }

  // ---- Session ----
  createSession(locale: string): PersistedSession {
    const s: PersistedSession = {
      id: newSessionId(),
      userId: this.userId,
      brief: emptyBrief(),
      version: 1,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      isFork: false,
    };
    this.data.sessions.push(s);
    this.save();
    return s;
  }

  /**
   * 最近一轮如果**是澄清轮**，返回它给出的选项；否则返回空数组。
   *
   * 用于识别"用户在回应上一轮的澄清选项"（打字回「1」/「A」）。
   * 判定依据是最近一条 response_completed 的 mode：
   *   - mode !== "clarify" → 上一轮是讲解，用户打「1」不该被当成选项
   *   - mode === "clarify" → 再从同一 turnId 的 clarification_asked 事件取出选项
   * 不能直接找最后一条 clarification_asked：更早的澄清轮会残留，导致误判。
   */
  pendingClarificationOptions(sessionId: string): ClarificationOption[] {
    // 事件表本身不带 sessionId（与后端不同），因此用 turnId 归属来界定本会话，
    // turnId -> session 的映射取自 messages。
    const turnIds = new Set(
      this.data.messages.filter((m) => m.sessionId === sessionId).map((m) => m.turnId),
    );
    if (turnIds.size === 0) return [];

    let lastTurnId: string | undefined;
    for (const event of [...this.data.events].reverse()) {
      if (event.kind !== "response_completed" || !turnIds.has(event.turnId)) continue;
      if (event.payload.mode !== "clarify") return [];
      lastTurnId = event.turnId;
      break;
    }
    if (!lastTurnId) return [];

    for (const event of [...this.data.events].reverse()) {
      if (event.turnId !== lastTurnId || event.kind !== "clarification_asked") continue;
      const options = event.payload.options;
      return Array.isArray(options) ? (options as ClarificationOption[]) : [];
    }
    return [];
  }

  /** 最近一个未分叉的会话，用于启动时恢复上次对话。 */
  latestResumableSession(): PersistedSession | undefined {
    return [...this.data.sessions]
      .filter((s) => s.userId === this.userId && !s.isFork)
      .sort((a, b) => b.updatedAt - a.updatedAt)[0];
  }

  /** 某会话的历史轮次（用户输入 + 助手正文），按时间顺序，供启动时回放。 */
  sessionTurns(sessionId: string): Array<{ userText: string; assistantText: string }> {
    const byTurn = new Map<string, { userText: string; assistantText: string }>();
    const order: string[] = [];
    for (const message of this.data.messages) {
      if (message.sessionId !== sessionId) continue;
      let entry = byTurn.get(message.turnId);
      if (!entry) {
        entry = { userText: "", assistantText: "" };
        byTurn.set(message.turnId, entry);
        order.push(message.turnId);
      }
      if (message.role === "user") entry.userText = message.content;
      else entry.assistantText = message.content;
    }
    return order
      .map((turnId) => byTurn.get(turnId)!)
      .filter((t) => t.userText || t.assistantText);
  }

  /** 删除一个空会话（启动时先建后弃，避免留下无消息的孤儿会话）。 */
  deleteSession(sessionId: string): void {
    const before = this.data.sessions.length;
    this.data.sessions = this.data.sessions.filter((s) => s.id !== sessionId);
    if (this.data.sessions.length !== before) this.save();
  }

  getSession(sessionId: string): PersistedSession | undefined {
    return this.data.sessions.find((s) => s.id === sessionId && s.userId === this.userId);
  }

  updateBrief(sessionId: string, brief: SessionBrief): void {
    const s = this.getSession(sessionId);
    if (s) {
      s.brief = brief;
      s.version += 1;
      s.updatedAt = Date.now();
      this.save();
    }
  }

  createSessionFork(sourceSessionId: string): PersistedSession[] {
    const source = this.getSession(sourceSessionId);
    if (!source) return [];
    const activeMemIds = this.data.memories
      .filter((m) => m.userId === this.userId && m.status === "active")
      .sort((a, b) => (a.id < b.id ? -1 : 1))
      .map((m) => m.id);
    const conceptIds = this.data.conceptStates
      .filter((m) => m.userId === this.userId)
      .sort((a, b) => (a.id < b.id ? -1 : 1))
      .map((m) => m.id);

    const skipTurns = new Set<string>();
    const sourceMsgs = this.data.messages.filter((m) => m.sessionId === sourceSessionId);
    for (const msg of sourceMsgs) {
      if (msg.role === "user" && msg.content.length <= 40 && classifyFeedback(msg.content).kind !== "none") {
        skipTurns.add(msg.turnId);
      }
    }

    const groupId = newMemoryId();
    const forks: PersistedSession[] = [];
    for (const mode of ["on", "off"] as const) {
      const s: PersistedSession = {
        id: newSessionId(),
        userId: this.userId,
        brief: {
          ...source.brief,
          knownPropositions: [...source.brief.knownPropositions],
          openQuestions: [...source.brief.openQuestions],
          effectiveExplanations: [...source.brief.effectiveExplanations],
          failedExplanations: [...source.brief.failedExplanations],
          exactAnchors: [...source.brief.exactAnchors],
          sessionRules: [...source.brief.sessionRules],
        },
        version: 1,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        isFork: true,
        memoryMode: mode,
        memorySnapshotIds: [...activeMemIds],
        conceptSnapshotIds: [...conceptIds],
        memorySnapshot: structuredClone(this.data.memories.filter((m) => activeMemIds.includes(m.id))),
        conceptSnapshot: structuredClone(this.data.conceptStates.filter((s) => conceptIds.includes(s.id))),
        forkGroupId: groupId,
      };
      this.data.sessions.push(s);
      forks.push(s);
    }
    // copy messages (exclude skipped turns, remap turn ids)
    for (const fork of forks) {
      const turnMap = new Map<string, string>();
      for (const msg of sourceMsgs) {
        if (skipTurns.has(msg.turnId)) continue;
        if (!turnMap.has(msg.turnId)) turnMap.set(msg.turnId, newTurnId());
        this.data.messages.push({
          ...msg,
          id: newMemoryId(),
          sessionId: fork.id,
          turnId: turnMap.get(msg.turnId)!,
        });
      }
    }
    this.save();
    return forks;
  }

  getFork(sessionId: string): PersistedSession | undefined {
    return this.data.sessions.find((s) => s.id === sessionId && s.userId === this.userId && s.isFork);
  }

  // ---- Messages ----
  saveMessage(sessionId: string, turnId: string, role: Message["role"], content: string): void {
    const existing = this.data.messages.find((m) => m.sessionId === sessionId && m.turnId === turnId && m.role === role);
    if (existing) {
      existing.content = content;
      existing.createdAt = Date.now();
      this.save();
      return;
    }
    this.data.messages.push({ id: newMemoryId(), sessionId, turnId, role, content, createdAt: Date.now() });
    this.save();
  }
  recentMessages(sessionId: string, limit = 8): Array<{ role: string; content: string }> {
    if (limit <= 0) return [];
    return this.messagesFor(sessionId)
      .slice(-limit)
      .map((m) => ({ role: m.role, content: m.content }));
  }
  messagesFor(sessionId: string): Message[] {
    return this.data.messages
      .filter((m) => m.sessionId === sessionId)
      .sort((a, b) => a.createdAt - b.createdAt);
  }

  // ---- 事件审计 ----
  allEvents(): LedgerEvent[] {
    return [...this.data.events];
  }

  listSessions(): PersistedSession[] {
    return this.data.sessions.filter((s) => s.userId === this.userId);
  }
}
