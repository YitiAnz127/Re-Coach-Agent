import type { ConceptState, ResolvedTask, TeachingLevel, TeachingRating, TeachingStart } from "../types.js";

export function inferTeachingStart(
  task: ResolvedTask,
  userText: string,
  states: ConceptState[],
  calibration?: { level: TeachingLevel },
): TeachingStart {
  const base = { domain: task.domain, concept: task.concept };
  if (/(我是新手|第一次学|没学过|零基础|从零开始|小白|我不懂|我不会)/.test(userText)) {
    return { ...base, level: "novice", source: "current_explicit" };
  }
  if (/(我熟悉|我掌握|我已经掌握|跳过基础|不用讲基础)/.test(userText)) {
    return { ...base, level: "advanced", source: "current_explicit" };
  }
  if (/(我知道|我学过|我了解|我会)/.test(userText)) {
    return { ...base, level: "familiar", source: "current_explicit" };
  }
  if (task.concept && calibration && calibration.level !== "unknown") {
    return { ...base, level: calibration.level, source: "concept_feedback" };
  }
  for (const state of states) {
    if (state.concept !== task.concept || state.proposition !== task.proposition) continue;
    if (state.state === "self_reported_understood" && state.evidenceKind === "user_self_report") {
      return { ...base, level: "familiar", source: "proposition_state" };
    }
    if (state.state === "unresolved" && state.evidenceKind === "user_explicit_unresolved") {
      return { ...base, level: "novice", source: "proposition_state" };
    }
  }
  return { ...base, level: "unknown", source: "unknown" };
}

export function adjustTeachingLevel(base: TeachingLevel, rating: TeachingRating): TeachingLevel {
  const levels: TeachingLevel[] = ["novice", "familiar", "advanced"];
  if (rating === "just_right") return base;
  if (base === "unknown") return rating === "too_basic" ? "familiar" : "novice";
  const index = levels.indexOf(base);
  return levels[rating === "too_basic" ? Math.min(index + 1, 2) : Math.max(index - 1, 0)]!;
}
