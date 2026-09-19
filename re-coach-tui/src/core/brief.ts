// Session Brief / Concept State（与后端 brief.py 1:1 对齐）
import type { ConceptState, ResolvedTask, SessionBrief, TurnMode } from "../types.js";
import { newMemoryId } from "../ids.js";

export interface BriefStore {
  listConceptStates(userId: string, concept: string, domain: string, limit: number): ConceptState[];
  upsertConceptState(s: ConceptState): string;
}

export interface ApplyTurnDeltaArgs {
  userText: string;
  focus: string;
  mode: TurnMode;
  task?: ResolvedTask;
  clarificationQuestion?: string;
  fromFeedback?: boolean;
}

export function applyTurnDelta(brief: SessionBrief, args: ApplyTurnDeltaArgs): SessionBrief {
  const updated: SessionBrief = {
    ...brief,
    knownPropositions: [...brief.knownPropositions],
    openQuestions: [...brief.openQuestions],
    effectiveExplanations: [...brief.effectiveExplanations],
    failedExplanations: [...brief.failedExplanations],
    exactAnchors: [...brief.exactAnchors],
    sessionRules: [...brief.sessionRules],
  };

  if (args.fromFeedback) {
    updated.clarifyStreak = 0;
    return updated;
  }

  if (args.mode === "clarify") {
    updated.clarifyStreak += 1;
    const question =
      (args.clarificationQuestion ?? "").trim() || `需要进一步明确：${args.userText.slice(0, 120)}`;
    updated.openQuestions = [question.slice(0, 240)];
  } else {
    updated.clarifyStreak = 0;
    if (args.task && args.task.goal) {
      updated.goal = args.task.goal.slice(0, 120);
    }
  }
  if (args.focus) updated.currentFocus = args.focus;
  if (!updated.goal && args.mode === "explain") {
    updated.goal = args.userText.slice(0, 120);
  }

  if (args.task && args.task.proposition) {
    if (/(我知道|已经懂|理解了|掌握了)/.test(args.userText)) {
      updated.knownPropositions = dedupe([...updated.knownPropositions, args.task.proposition.slice(0, 200)]).slice(-8);
    }
    if (/(不懂|不理解|没懂|还是不会|卡住|不明白)/.test(args.userText)) {
      updated.openQuestions = dedupe([...updated.openQuestions, args.task.proposition.slice(0, 200)]).slice(0, 3);
    }
    if (/(讲得很好|很清楚|有帮助|这样讲我懂了|明白了)/.test(args.userText)) {
      updated.effectiveExplanations = dedupe([...updated.effectiveExplanations, args.focus.slice(0, 120)]).slice(-8);
    }
    if (/(没讲清|没帮助|还是不懂|不太明白)/.test(args.userText)) {
      updated.failedExplanations = dedupe([...updated.failedExplanations, args.focus.slice(0, 120)]).slice(-8);
    }
    if (/(公式|\$|代码|pytorch|\b[a-zA-Z_]+\([^)]*\))/.test(args.userText)) {
      updated.exactAnchors = dedupe([...updated.exactAnchors, args.userText.slice(0, 240)]).slice(-8);
    }
  }
  return updated;
}

function dedupe(arr: string[]): string[] {
  return Array.from(new Set(arr));
}

export function inferConceptState(userText: string): { state: string; evidenceKind: string } {
  if (/(你说错|不对|应该是|纠正|不是这样)/.test(userText)) {
    return { state: "corrected", evidenceKind: "user_explicit_correction" };
  }
  if (/(还是不懂|没懂|不理解|不明白|卡住)/.test(userText)) {
    return { state: "unresolved", evidenceKind: "user_explicit_unresolved" };
  }
  if (/(我懂了|明白了|理解了|讲清楚了|有帮助)/.test(userText)) {
    return { state: "self_reported_understood", evidenceKind: "user_self_report" };
  }
  return { state: "introduced", evidenceKind: "agent_observation" };
}

export function recordConceptState(
  store: BriefStore,
  userId: string,
  task: ResolvedTask,
  args: { state: string; evidenceKind: string; sourceEventId: string },
): string | null {
  if (!task.concept || !task.proposition) return null;
  const stateId = newMemoryId();
  const t = Date.now();
  return store.upsertConceptState({
    id: stateId,
    userId,
    domain: task.domain,
    concept: task.concept,
    proposition: task.proposition,
    state: args.state,
    evidenceKind: args.evidenceKind,
    sourceEventId: args.sourceEventId,
    createdAt: t,
    updatedAt: t,
  });
}
