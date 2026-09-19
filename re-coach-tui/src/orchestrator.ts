// Turn 编排（与后端 orchestrator.py 1:1 对齐，事件通过回调下发）
import type { AppConfig } from "./config.js";
import type { Store } from "./store.js";
import type { ResolvedTask, SessionBrief, TurnPresentation } from "./types.js";
import { newTurnId } from "./ids.js";
import { makeEvent } from "./core/events.js";
import {
  classifyFeedback,
  forgetMemories,
  retrieve,
  scopeLabel,
  writeMemory,
} from "./core/memory.js";
import { runGate, detectConcept } from "./core/gate.js";
import { resolveOptionSelection } from "./core/selection.js";
import { compileContext, stripInternalMarkers } from "./core/compiler.js";
import { streamExplanation, newCoachMeta, verifyOutput, splitChunks } from "./core/coach.js";
// stripInternalMarkers 已在上方与 compileContext 一起导入
import {
  applyTurnDelta,
  inferConceptState,
  recordConceptState,
} from "./core/brief.js";
import type { AgentSession, AgentTurnEvent } from "./agent.js";

interface SuggestedAction {
  id: string;
  label: string;
  prompt: string;
}

function suggestedActions(concept: string, scope: string): SuggestedAction[] {
  const topic = concept || "这个知识点";
  const actions: SuggestedAction[] = [
    { id: "deeper", label: "再深入一层", prompt: `再深入一层讲讲${topic}的机制，用更细的例子。` },
    { id: "another-angle", label: "换个角度讲", prompt: `换一个角度重新讲${topic}，用和刚才不同的切入点。` },
  ];
  if (scope !== "代码实现") {
    actions.push({ id: "code", label: "看最小代码示例", prompt: `给我看${topic}的最小代码示例，逐行解释。` });
  }
  return actions.slice(0, 3);
}

function globalRules(store: Store, userId: string, memoryIds?: Set<string>, snapshot?: ReturnType<Store["queryAllActiveMemories"]>): ReturnType<typeof import("./core/memory.js")["retrieve"]>["selected"] {
  const rules = (snapshot ?? store.listMemories(userId, { status: "active", type: "interaction_rule" })).filter(
    (m) => m.userId === userId && m.type === "interaction_rule" && m.status === "active" &&
      (memoryIds === undefined || memoryIds.has(m.id)) &&
      m.domain === "*" &&
      m.conceptScope === "*" &&
      m.propositionScope === "*" &&
      m.taskScope === "*",
  );
  return rules.slice(0, 2);
}

function retrospective(task: ResolvedTask, plan: string[]) {
  const concept = task.concept || "本轮问题";
  const known = task.knownContext.length > 0 ? task.knownContext[task.knownContext.length - 1]! : "已知前置";
  const openQuestions = task.openQuestions.slice(0, 3).length
    ? task.openQuestions.slice(0, 3)
    : [`是否需要继续深入${concept}的边界？`];
  return {
    connections: [`把${concept}连接到${known}，再从问题场景回到机制`],
    openQuestions,
    approach: plan.slice(0, 3).length ? plan.slice(0, 3) : ["定位问题", "建立直觉", "连接机制"],
  };
}

function knowledgeUnitClosed(userText: string): boolean {
  return inferConceptState(userText).state === "self_reported_understood" ||
    inferConceptState(userText).state === "corrected";
}

export interface RunTurnOptions {
  cfg: AppConfig;
  store: Store;
  session: AgentSession;
  userText: string;
  onEvent: (e: AgentTurnEvent) => void;
}

export async function runTurn(opts: RunTurnOptions): Promise<void> {
  const turnId = newTurnId();
  try {
    await executeTurn(opts, turnId);
  } catch {
    opts.onEvent({ type: "turn.error", turnId, code: "INTERNAL", message: "本轮处理或保存失败，请检查存储后重试。" });
  }
}

async function executeTurn(opts: RunTurnOptions, turnId: string): Promise<void> {
  const { cfg, store, session, userText: rawUserText, onEvent } = opts;
  // 可重新绑定：澄清选项的「打字选择」会把「1」改写成该选项的完整表述
  let userText = rawUserText;
  const userId = cfg.user;
  const fork = store.getFork(session.id);
  const isFork = fork !== undefined;
  const effectiveMemoryOn = isFork ? fork!.memoryMode === "on" : cfg.memoryOn;
  const memoryMutationsAllowed = !isFork;
  const forkMemoryIds = isFork ? new Set(fork!.memorySnapshotIds ?? []) : undefined;
  const forkConceptIds = isFork ? new Set(fork!.conceptSnapshotIds ?? []) : undefined;
  const turnStartedAt = performance.now();
  let assistantParts: string[] = [];
  let mode: "clarify" | "explain" = "explain";
  let fromFeedback = false;
  let context: ReturnType<typeof compileContext> | null = null;
  let retrieval: ReturnType<typeof retrieve> | null = null;

  const persisted = store.getSession(session.id);
  if (!persisted) {
    onEvent({ type: "turn.error", turnId, code: "SESSION_NOT_FOUND", message: "会话不存在" });
    return;
  }
  let brief = persisted.brief;

  store.logEvent(makeEvent({ userId, turnId, kind: "turn_started", payload: { textLen: userText.length } }));
  const recentForGate = store.recentMessages(session.id, 4);
  store.saveMessage(session.id, turnId, "user", userText);

  // ---- 澄清选项的「打字选择」识别（与后端 selection.py 对齐）----
  // 澄清轮把选项标成 A/B/C/D/E，但用户经常直接打字回「1」或「A」。
  // 不识别的话会被当成全新问题，又抛出一整篇泛泛的讲解。
  // 原始输入已在上方落库（界面显示用户真正打的字），这里只改写后续环节
  // 看到的语义文本，让「1」得到与点选第一项完全一致的讲解。
  const selected = resolveOptionSelection(
    userText,
    store.pendingClarificationOptions(session.id),
  );
  if (selected) {
    store.logEvent(
      makeEvent({
        userId,
        turnId,
        kind: "clarification_resolved",
        payload: { via: "typed_selector", rawLen: userText.length },
      }),
    );
    userText = selected;
  }

  // ---- 第一级反馈门控 ----
  const feedback = classifyFeedback(userText);
  let feedbackNote = "";
  if (feedback.kind !== "none") {
    const fbEvent = store.logEvent(makeEvent({ userId, turnId, kind: "feedback_received", payload: { kind: feedback.kind, polarity: feedback.polarity } }));
    if (effectiveMemoryOn && memoryMutationsAllowed && feedback.kind === "write_longterm") {
      const [fbDomain, fbConcept] = detectConcept(userText);
      const written = writeMemory(store, userId, {
        type: feedback.memoryType,
        rule: feedback.rule,
        domain: fbConcept ? fbDomain : "*",
        conceptScope: fbConcept || "*",
        polarity: feedback.polarity,
        evidenceKind: "user_explicit_longterm",
        confidence: 0.9,
        sourceEventId: fbEvent.id,
      });
      store.logEvent(makeEvent({ userId, turnId, kind: "memory_written", payload: { memoryId: written.id, type: written.type, scope: scopeLabel(written) } }));
      feedbackNote = `已记住：${written.rule}`;
    } else if (effectiveMemoryOn && memoryMutationsAllowed && feedback.kind === "forget") {
      const forgotten = forgetMemories(store, userId, feedback.keyword, fbEvent.id);
      store.logEvent(makeEvent({ userId, turnId, kind: "memory_archived", payload: { forgotten, keyword: feedback.keyword } }));
      feedbackNote = forgotten.length > 0 ? `已遗忘 ${forgotten.length} 条相关偏好。` : "没有找到匹配的已保存偏好。";
    } else if ((!effectiveMemoryOn || !memoryMutationsAllowed) && (feedback.kind === "write_longterm" || feedback.kind === "forget")) {
      feedbackNote = memoryMutationsAllowed
        ? "当前已关闭长期记忆，本轮没有读取或改动长期偏好。"
        : "当前对照分支为只读，本轮没有改动长期偏好。";
    } else if (feedback.kind === "session_only") {
      if (!brief.sessionRules.includes(feedback.rule)) brief.sessionRules.push(feedback.rule);
      brief.sessionRules = brief.sessionRules.slice(-4);
      feedbackNote = "好的，这条只在本会话生效，不会写入长期记忆。";
    }
  }

  // ---- 澄清门控 ----
  const globals = effectiveMemoryOn ? globalRules(store, userId, forkMemoryIds, fork?.memorySnapshot) : [];
  const contextHints = [brief.goal, brief.currentFocus].filter(Boolean) as string[];
  for (const msg of recentForGate) if (msg.role === "user") contextHints.push(msg.content);
  for (const r of globals) contextHints.push(r.rule);

  const gate = runGate(userText, { clarifyStreak: brief.clarifyStreak, knownContext: contextHints.filter(Boolean) });

  if (feedbackNote && (feedback.kind === "write_longterm" || feedback.kind === "forget") && userText.length <= 40) {
    gate.decision = "READY";
    gate.focus = "偏好已更新";
    gate.plan = ["确认反馈处理结果"];
    fromFeedback = true;
  }

  onEvent({
    type: "turn.started",
    turnId,
    mode: gate.decision === "NEEDS_CLARIFICATION" ? "clarify" : "explain",
    focus: gate.focus,
    plan: gate.plan,
  });

  // ---- 澄清轮 ----
  if (gate.decision === "NEEDS_CLARIFICATION") {
    mode = "clarify";
    store.logEvent(makeEvent({ userId, turnId, kind: "clarification_asked", payload: { question: (gate.question ?? "").slice(0, 200), options: gate.options } }));
    for (const chunk of splitChunks(gate.question ?? "")) {
      assistantParts.push(chunk);
      onEvent({ type: "assistant.delta", turnId, delta: chunk });
    }
    const presentation: TurnPresentation = {
      mode,
      depth: "auto",
      focus: gate.focus,
      plan: gate.plan,
      personalization: [],
      metrics: { timeToFirstTokenMs: Math.round(performance.now() - turnStartedAt), memorySearchMs: 0, contextCompileMs: 0, memoryCapsuleTokens: 0, totalInputTokens: 0 },
      clarificationOptions: gate.options,
      suggestedActions: [],
      truncated: false,
    };
    finalizeTurn(opts, { mode, brief, turnId, userText, assistantText: assistantParts.join(""), focus: gate.focus, presentation, clarificationQuestion: gate.question ?? "", memoryEnabled: effectiveMemoryOn, memoryMutationsAllowed, fromFeedback, task: gate.task });
    onEvent({ type: "turn.completed", turnId, presentation });
    return;
  }

  // ---- READY / ANSWER_WITH_ASSUMPTION ----
  const task = gate.task;
  let systemPrompt: string;
  let userPrompt: string;
  let appliedLabels: string[] = [];

  if (fromFeedback) {
    systemPrompt = "你是「知返 Re:Coach」的偏好确认助手。用户刚提供了一条学习偏好或反馈，系统已经处理（记住/遗忘/会话规则）。请用一句话自然确认结果，不展开讲解、不提问、不引入任何教学话题。";
    userPrompt = `用户反馈：${userText}`;
  } else {
    retrieval = retrieve(store, userId, task, { memoryOn: effectiveMemoryOn, memoryIds: forkMemoryIds, memories: fork?.memorySnapshot, memoryMaxSelected: cfg.memoryMaxSelected, memoryHardLimit: cfg.memoryHardLimit });
    if (globals.length > 0) {
      const known = new Set(retrieval.selected.map((m) => m.id));
      for (const r of globals) if (!known.has(r.id)) retrieval.selected.push(r);
    }
    if (effectiveMemoryOn) {
      store.logEvent(makeEvent({ userId, turnId, kind: "memory_recalled", payload: { candidates: retrieval.recalledIds }, latencyMs: retrieval.searchMs }));
    }
    const states = effectiveMemoryOn ? (fork?.conceptSnapshot ?? store.listConceptStates(userId, task.concept, task.domain, Number.MAX_SAFE_INTEGER))
      .filter((s) => s.userId === userId && (s.concept === task.concept || (!task.concept && s.domain === task.domain)) && (!forkConceptIds || forkConceptIds.has(s.id)))
      .sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 5) : [];
    const recent = store.recentMessages(session.id);
    context = compileContext({
      task,
      brief,
      selectedMemories: retrieval.selected,
      conceptStates: states,
      recentMessages: recent,
      assumption: gate.assumption,
      sessionOnlyRules: brief.sessionRules,
      memoryCapsuleTokens: cfg.memoryCapsuleTokens,
    });
    if (effectiveMemoryOn) {
      store.logEvent(makeEvent({ userId, turnId, kind: "memory_selected", payload: { selected: context.trace.selected, overridden: context.trace.overridden } }));
    }
    store.logEvent(makeEvent({ userId, turnId, kind: "context_compiled", payload: { ...context.trace, capsuleTokens: context.capsuleTokens }, tokenCount: context.totalInputTokens, latencyMs: context.compileMs }));
    appliedLabels = context.applied.map((a) => a.effect);
    systemPrompt = context.system;
    userPrompt = context.user;
  }

  const meta = newCoachMeta();
  const prefix = feedbackNote ? feedbackNote + "\n\n" : "";
  let firstDeltaAt: number | null = null     ;
  try {
    for await (const d of streamExplanation(cfg, { system: systemPrompt, user: userPrompt, task, appliedLabels, meta })) {
      if (d.kind === "thinking") {
        onEvent({ type: "assistant.thinking", turnId, delta: d.delta });
        continue;
      }
      // 剥掉内部提示结构标记：模型偶尔会在正文里引用定界符，
      // 那会向用户暴露内部提示结构。系统提示已要求不要提及，这里是输出侧兜底。
      // 必须放在 push/yield 之前，否则流式增量与落库正文会不一致。
      const content = stripInternalMarkers(d.delta);
      if (!content) continue;
      if (firstDeltaAt === null) {
        firstDeltaAt = performance.now();
        if (prefix) {
          assistantParts.push(prefix);
          onEvent({ type: "assistant.delta", turnId, delta: prefix });
        }
      }
      assistantParts.push(content);
      onEvent({ type: "assistant.delta", turnId, delta: content });
    }
  } catch {
    onEvent({ type: "turn.error", turnId, code: "INTERNAL", message: "生成回答时发生内部错误" });
    return;
  }

  const ttftMs = firstDeltaAt !== null ? Math.round(firstDeltaAt - turnStartedAt) : Math.round(performance.now() - turnStartedAt);
  if (assistantParts.length === 0) {
    const fallback = "抱歉，我这次没能组织好回答。请换个说法再问我一次，或先告诉我你卡在哪一步。";
    assistantParts.push(fallback);
    onEvent({ type: "assistant.delta", turnId, delta: fallback });
  }
  store.logEvent(makeEvent({ userId, turnId, kind: "model_called", payload: { provider: meta.provider, model: meta.model, fallback: meta.fallback, thinkingTtftMs: meta.thinkingTtftMs, continuationCount: meta.continuationCount, contentTtftMs: meta.contentTtftMs }, latencyMs: meta.ttftMs }));

  const depth = task.desiredDepth !== "auto" ? task.desiredDepth : "L2";
  const presentation: TurnPresentation = {
    mode: "explain",
    depth,
    focus: gate.focus,
    truncated: meta.truncated,
    plan: gate.plan,
    personalization: context ? context.applied.map(({ memory, effect }) => ({ memoryId: memory.id, label: memory.rule.slice(0, 40), scope: scopeLabel(memory), effect })) : [],
    metrics: {
      timeToFirstTokenMs: ttftMs,
      memorySearchMs: fromFeedback ? 0 : retrieval?.searchMs ?? 0,
      contextCompileMs: fromFeedback ? 0 : context?.compileMs ?? 0,
      memoryCapsuleTokens: fromFeedback ? 0 : context?.capsuleTokens ?? 0,
      totalInputTokens: fromFeedback ? 0 : context?.totalInputTokens ?? 0,
      // 如实上报本轮实际 provider 与降级状态。只写内部 meta 而不进 presentation，
      // 界面上就无从判断——用户读着模板文本却以为来自模型。
      provider: meta.provider,
      model: meta.model,
      fallback: meta.fallback,
      fallbackReason: meta.fallbackReason,
      requestedModel: meta.requestedModel,
    },
    clarificationOptions: undefined,
    suggestedActions: suggestedActions(task.concept, task.taskScope).map((a) => a.label),
    retrospective: knowledgeUnitClosed(userText) ? retrospective(task, gate.plan) : undefined,
    outputVerification: verifyOutput(task, assistantParts.join("")),
  };

  finalizeTurn(opts, { mode, brief, turnId, userText, assistantText: assistantParts.join(""), focus: gate.focus, presentation, task, memoryEnabled: effectiveMemoryOn, memoryMutationsAllowed, fromFeedback, startedAt: turnStartedAt });
  onEvent({ type: "turn.completed", turnId, presentation });
}

function finalizeTurn(
  opts: RunTurnOptions,
  args: {
    mode: "clarify" | "explain";
    brief: SessionBrief;
    turnId: string;
    userText: string;
    assistantText: string;
    focus: string;
    presentation: TurnPresentation;
    task?: ResolvedTask;
    clarificationQuestion?: string;
    memoryEnabled: boolean;
    memoryMutationsAllowed: boolean;
    fromFeedback: boolean;
    startedAt?: number;
  },
): void {
  const { cfg, store, session } = opts;
  const userId = cfg.user;
  const { mode, brief, turnId, userText, assistantText, focus, presentation, task, clarificationQuestion, memoryEnabled, memoryMutationsAllowed, fromFeedback, startedAt } = args;

  try {
    store.saveMessage(session.id, turnId, "assistant", assistantText);
    if (mode === "explain" && brief.clarifyStreak > 0) {
      store.logEvent(makeEvent({ userId, turnId, kind: "clarification_resolved", payload: { focus } }));
    }
    const updated = applyTurnDelta(brief, { userText, focus, mode, task, clarificationQuestion, fromFeedback });
    store.updateBrief(session.id, updated);
    store.logEvent(makeEvent({ userId, turnId, kind: "session_brief_updated", payload: { focus } }));
  } catch (error) {
    store.logEvent(makeEvent({ userId, turnId, kind: "turn_failed", payload: { stage: "brief_update" } }));
    throw error;
  }

  if (!fromFeedback && memoryEnabled && memoryMutationsAllowed && task && task.concept) {
    try {
      const { state, evidenceKind } = inferConceptState(userText);
      const evt = store.logEvent(makeEvent({ userId, turnId, kind: "concept_state_updated", payload: { concept: task.concept, state, evidenceKind } }));
      recordConceptState(store, userId, task, { state, evidenceKind, sourceEventId: evt.id });
    } catch {
      // 忽略：概念状态是可降级旁路
    }
  }

  try {
    // payload.mode 用于判断"上一轮是不是澄清轮"——用户随后打「1」时据此决定
    // 是否当成选项选择，而不是新问题。
    store.logEvent(makeEvent({ userId, turnId, kind: "response_completed", payload: { chars: assistantText.length, mode }, latencyMs: startedAt !== undefined ? Math.round(performance.now() - startedAt) : undefined }));
  } catch {
    // 忽略
  }
}
