from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from ..config import get_settings
from ..errors import provider_failure_message
from ..schemas import (
    Metrics,
    PersonalizationItem,
    SuggestedAction,
    Retrospective,
    OutputVerification,
    TurnPresentation,
    sse_event,
)
from . import brief as brief_service
from . import coach as coach_service
from . import compiler as compiler_service
from . import events as event_service
from . import gate as gate_service
from . import memory as memory_service
from . import selection as selection_service
from . import turns as turn_store


def _suggested_actions(concept: str, scope: str) -> list[SuggestedAction]:
    """可选的后续动作：始终可跳过，不构成强制测验。"""
    topic = concept or "这个知识点"
    actions = [
        SuggestedAction(
            id="deeper",
            label="再深入一层",
            prompt=f"再深入一层讲讲{topic}的机制，用更细的例子。",
        ),
        SuggestedAction(
            id="another-angle",
            label="换个角度讲",
            prompt=f"换一个角度重新讲{topic}，用和刚才不同的切入点。",
        ),
    ]
    if scope != "代码实现":
        actions.append(
            SuggestedAction(
                id="code",
                label="看最小代码示例",
                prompt=f"给我看{topic}的最小代码示例，逐行解释。",
            )
        )
    return actions[:3]


def _global_rules(user_id: str, memory_ids: set[str] | None = None, snapshot: list[memory_service.Memory] | None = None) -> list[memory_service.Memory]:
    """澄清前允许的最小预读：最多 1-2 条稳定全局交互规则（全部作用域为 *）。"""
    rows = snapshot if snapshot is not None else memory_service.list_memories(user_id, type_="interaction_rule")
    return [
        m for m in rows
        if m.user_id == user_id and m.status == "active" and m.type == "interaction_rule"
        and (memory_ids is None or m.id in memory_ids)
        and all(getattr(m, f) == "*" for f in memory_service.SCOPE_FIELDS)
    ][:2]


def _retrospective(task, plan: list[str]) -> Retrospective:
    concept = task.concept or "本轮问题"
    known = task.known_context[-1] if task.known_context else "已知前置"
    open_questions = task.open_questions[:3] or [f"是否需要继续深入{concept}的边界？"]
    return Retrospective(
        connections=[f"把{concept}连接到{known}，再从问题场景回到机制"],
        openQuestions=open_questions,
        approach=plan[:3] or ["定位问题", "建立直觉", "连接机制"],
    )


def _knowledge_unit_closed(user_text: str) -> bool:
    """知识单元是否自然结束：只看用户本轮是否给出收尾信号。

    复用 brief.infer_concept_state 的同一套语言信号，避免两套判定漂移：
    用户自报理解（我懂了/明白了/理解了/讲清楚了/有帮助）或明确纠偏
    （你说错/不对/应该是/纠正/不是这样）→ 本轮是单元的收尾轮，才生成复盘。
    普通提问、开场问候（如"你好"）、未解（还是不懂）都不算单元结束，
    结构化复盘卡片不在这些轮出现；每次回答末尾的轻量文字复盘由主 Coach 负责。
    """
    state, _ = brief_service.infer_concept_state(user_text, "")
    return state in ("self_reported_understood", "corrected")


async def run_turn(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    user_text: str,
    request_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """单个 Turn 的完整流水线（v0.6 §6.3 顺序不可变）：

    最小预读 → 反馈门控 → Clarification Gate →（完整检索 → 编译 → 单次主 Coach）→ 异步状态更新。
    产生 SSE 事件字典；每条流只有一个终止事件。
    """
    settings = get_settings()
    fork = brief_service.get_session_fork(session_id)
    is_fork = fork is not None
    effective_memory_on = fork["memory_mode"] == "on" if is_fork else settings.memory_on
    effective_memory_mode = fork["memory_mode"] if is_fork else "default"
    memory_mutations_allowed = not is_fork
    fork_memory_ids = set(json.loads(fork["memory_snapshot_json"])) if is_fork else None
    fork_concept_ids = set(json.loads(fork["concept_snapshot_json"])) if is_fork else None
    memory_snapshot = (
        [memory_service._row_to_memory(row) for row in json.loads(fork["memory_content_json"])]
        if is_fork and fork.get("memory_content_json") is not None else None
    )
    concept_snapshot = (
        json.loads(fork["concept_content_json"])
        if is_fork and fork.get("concept_content_json") is not None else None
    )
    turn_started_at = time.perf_counter()
    assistant_parts: list[str] = []
    mode = "explain"
    canonical_committed = False
    from_feedback = False

    try:
        session = brief_service.get_session(session_id)
        if session is None:
            from ..errors import sse_error
            yield sse_error("SESSION_NOT_FOUND", turn_id=turn_id, request_id=request_id)
            return
        _, brief, _version = session

        event_service.log_event(
            user_id=user_id, session_id=session_id, turn_id=turn_id,
            mode="explain", kind="turn_started",
            payload={
                "text_len": len(user_text),
                "memoryMode": effective_memory_mode,
                "effectiveMemoryOn": effective_memory_on,
            },
        )
        recent_for_gate = brief_service.recent_messages(session_id, limit=4)
        brief_service.save_message(session_id, turn_id, "user", user_text)

        # ---- 澄清选项的「打字选择」识别 ----
        # 澄清轮把选项标成 A/B/C/D/E，但用户经常直接打字回「1」或「A」。
        # 不识别的话会被当成全新问题，又抛出一整篇泛泛的讲解
        # （2026-09-18 实测：打字回「1」产生 1438 字符，点选同项是 1184 字符且针对性完全不同）。
        # 原始输入已在上方落库（界面显示用户真正打的字），这里只改写后续环节看到
        # 的语义文本，让「1」得到与点选第一项完全一致的讲解。
        previous = turn_store.latest_completed_presentation(session_id)
        resolved = selection_service.resolve_option_selection(
            user_text, selection_service.parse_options(previous)
        )
        if resolved:
            event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode="explain", kind="clarification_resolved",
                payload={"via": "typed_selector", "rawLen": len(user_text)},
            )
            user_text = resolved

        # ---- 第一级反馈门控（本地规则；复杂蒸馏是 P1 异步任务）----
        feedback = memory_service.classify_feedback(user_text)
        feedback_note = ""
        if feedback.kind != "none":
            fb_event = event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode="explain", kind="feedback_received",
                payload={"kind": feedback.kind, "polarity": feedback.polarity},
            )
            if effective_memory_on and memory_mutations_allowed and feedback.kind == "write_longterm":
                fb_domain, fb_concept = gate_service._detect_concept(user_text)
                written = memory_service.write_memory(
                    user_id,
                    type_=feedback.memory_type,
                    rule=feedback.rule,
                    # 未提及具体主题的显式长期规则视为跨主题全局规则
                    domain=fb_domain if fb_concept else "*",
                    concept_scope=fb_concept or "*",
                    polarity=feedback.polarity,
                    evidence_kind="user_explicit_longterm",
                    confidence=0.9,
                    source_event_id=fb_event,
                )
                event_service.log_event(
                    user_id=user_id, session_id=session_id, turn_id=turn_id,
                    mode="explain", kind="memory_written",
                    payload={"memoryId": written.id, "type": written.type,
                             "scope": memory_service.scope_label(written)},
                )
                feedback_note = f"已记住：{written.rule}"
            elif effective_memory_on and memory_mutations_allowed and feedback.kind == "forget":
                forgotten = memory_service.forget_memories(user_id, feedback.keyword, fb_event)
                event_service.log_event(
                    user_id=user_id, session_id=session_id, turn_id=turn_id,
                    mode="explain", kind="memory_archived",
                    payload={"forgotten": forgotten, "keyword": feedback.keyword},
                )
                feedback_note = (
                    f"已遗忘 {len(forgotten)} 条相关偏好。" if forgotten else "没有找到匹配的已保存偏好。"
                )
            elif (not effective_memory_on or not memory_mutations_allowed) and feedback.kind in ("write_longterm", "forget"):
                feedback_note = (
                    "当前对照分支为只读，本轮没有改动长期偏好。"
                    if memory_mutations_allowed is False
                    else "当前已关闭长期记忆，本轮没有读取或改动长期偏好。"
                )
            elif feedback.kind == "session_only":
                if feedback.rule not in brief.session_rules:
                    brief.session_rules.append(feedback.rule)
                brief.session_rules = brief.session_rules[-4:]
                feedback_note = "好的，这条只在本会话生效，不会写入长期记忆。"

        # ---- 最小状态预读 → Clarification Gate ----
        # 概念检测的 known_context 只允许用户自己的消息与全局交互规则参与：
        # assistant 输出是系统生成的，若混入其中，用户下一条无概念词的消息
        # 可能被上一轮回复里的词汇（如"sigmoid"）带偏（最长词优先）。
        global_rules = _global_rules(user_id, fork_memory_ids, memory_snapshot) if effective_memory_on else []
        context_hints = [brief.goal, brief.current_focus]
        context_hints.extend(
            message["content"]
            for message in recent_for_gate
            if message["role"] == "user"
        )
        # 全局交互规则（作用域全为 *）也参与澄清前的意图/概念判定
        context_hints.extend(rule.rule for rule in global_rules)
        gate = gate_service.run_gate(
            user_text,
            clarify_streak=brief.clarify_streak,
            known_context=[item for item in context_hints if item],
        )

        if feedback_note and feedback.kind in ("write_longterm", "forget") and len(user_text) <= 40:
            # 纯反馈消息：确认即可，不强行展开讲解；且不做 brief 结构化提取，
            # 避免偏好文本进入 goal/current_focus/exact_anchors 泄漏给 fork off 分支。
            gate.decision = "READY"
            gate.focus = "偏好已更新"
            gate.plan = ["确认反馈处理结果"]
            from_feedback = True

        yield sse_event(
            "turn.started", turnId=turn_id,
            mode="clarify" if gate.decision == "NEEDS_CLARIFICATION" else "explain",
            focus=gate.focus, plan=gate.plan,
        )

        # ---- 澄清轮：无完整主题检索 ----
        if gate.decision == "NEEDS_CLARIFICATION":
            mode = "clarify"
            event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, kind="clarification_asked",
                payload={"question": gate.question[:200]},
            )
            for chunk in coach_service._split_chunks(gate.question):
                assistant_parts.append(chunk)
                yield sse_event("assistant.delta", turnId=turn_id, delta=chunk)

            presentation = TurnPresentation(
                mode="clarify",
                focus=gate.focus,
                plan=gate.plan,
                personalization=[],
                metrics=Metrics(
                    timeToFirstTokenMs=int((time.perf_counter() - turn_started_at) * 1000),
                    memorySearchMs=0, contextCompileMs=0,
                    memoryCapsuleTokens=0, totalInputTokens=0,
                ),
                clarificationOptions=gate.options,
                suggestedActions=[],
            )
            _finalize_turn(
                user_id=user_id, session_id=session_id, turn_id=turn_id, mode=mode,
                brief=brief, user_text=user_text, assistant_text="".join(assistant_parts),
                focus=gate.focus, presentation=presentation,
                started_at=turn_started_at,
                clarification_question=gate.question,
                memory_enabled=effective_memory_on,
                memory_mutations_allowed=memory_mutations_allowed,
                expected_version=_version,
                from_feedback=from_feedback,
            )
            canonical_committed = True
            yield sse_event(
                "turn.completed", turnId=turn_id,
                presentation=presentation.model_dump(exclude_none=True),
            )
            return

        # ---- READY / ANSWER_WITH_ASSUMPTION：问题完整后才做完整检索 ----
        task = gate.task
        if from_feedback:
            # 反馈轮：只确认，不教学。用轻量确认 prompt 替代完整教学上下文，
            # 避免模型拿到教学基线后自作主张展开讲解（实测曾答非所问讲 RoPE）。
            system_prompt = (
                "你是「知返 Re:Coach」的偏好确认助手。用户刚提供了一条学习偏好或反馈，"
                "系统已经处理（记住/遗忘/会话规则）。请用一句话自然确认结果，"
                "不展开讲解、不提问、不引入任何教学话题。"
            )
            user_prompt = f"用户反馈：{user_text}"
            context = None
            applied_labels: list[str] = []
        else:
            retrieval = memory_service.retrieve(
                user_id,
                task,
                memory_on=effective_memory_on,
                memory_ids=fork_memory_ids,
                snapshot=memory_snapshot,
            )
            if global_rules:
                known = {m.id for m in retrieval.selected}
                for m in global_rules:
                    if m.id not in known:
                        retrieval.selected.append(m)
            if effective_memory_on:
                event_service.log_event(
                    user_id=user_id, session_id=session_id, turn_id=turn_id,
                    mode=mode, kind="memory_recalled",
                    payload={"candidates": retrieval.recalled_ids},
                    latency_ms=retrieval.search_ms,
                )

            if not effective_memory_on:
                states = []
            elif concept_snapshot is not None:
                states = sorted(
                    [s for s in concept_snapshot if s["user_id"] == user_id
                     and (s["concept"] == task.concept or (not task.concept and s["domain"] == task.domain))],
                    key=lambda s: s["updated_at"], reverse=True,
                )[:5]
            else:
                states = brief_service.concept_states_for(user_id, task, state_ids=fork_concept_ids)
            recent = brief_service.recent_messages(session_id)
            context = compiler_service.compile_context(
                task=task,
                brief=brief,
                selected_memories=retrieval.selected,
                concept_states=states,
                recent_messages=recent,
                assumption=gate.assumption,
                session_only_rules=brief.session_rules,
            )
            if effective_memory_on:
                event_service.log_event(
                    user_id=user_id, session_id=session_id, turn_id=turn_id,
                    mode=mode, kind="memory_selected",
                    payload={"selected": context.trace["selected"],
                             "overridden": context.trace["overridden"]},
                )
            event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, kind="context_compiled",
                payload={**context.trace, "capsuleTokens": context.capsule_tokens},
                token_count=context.total_input_tokens, latency_ms=context.compile_ms,
            )
            applied_labels = [effect for _, effect in context.applied]
            system_prompt = context.system
            user_prompt = context.user
        meta = coach_service.CoachMeta()
        prefix = (feedback_note + "\n\n") if feedback_note else ""
        first_delta_at: float | None = None
        async for kind, delta in coach_service.stream_explanation(
            system=system_prompt, user=user_prompt, task=task,
            applied_labels=applied_labels, meta=meta,
        ):
            if kind == "thinking":
                # 模型推理片段：仅下发前端展示"思考中"，不进入回答正文、记忆或事件流水
                yield sse_event("assistant.thinking", turnId=turn_id, delta=delta)
                continue
            # kind == "content"：用户可见正文
            # 剥掉内部提示结构标记：模型偶尔会在正文里引用定界符
            # （实测出现过「<untrusted_memory> 里没保存住选项列表」这种句子），
            # 那会向用户暴露内部提示结构。系统提示已要求不要提及，
            # 这里是输出侧的兜底。必须放在 append/yield 之前，
            # 否则流式增量与落库正文会不一致。
            delta = compiler_service.strip_internal_markers(delta)
            if not delta:
                continue
            if first_delta_at is None:
                first_delta_at = time.perf_counter()
                if prefix:
                    assistant_parts.append(prefix)
                    yield sse_event("assistant.delta", turnId=turn_id, delta=prefix)
            assistant_parts.append(delta)
            yield sse_event("assistant.delta", turnId=turn_id, delta=delta)

        ttft_ms = (
            int((first_delta_at - turn_started_at) * 1000)
            if first_delta_at
            else int((time.perf_counter() - turn_started_at) * 1000)
        )
        # 最终兜底：模型即使经过续写仍未产出正文（极端情况），不静默发空白完成，
        # 给用户一句可操作的提示，保证前端永远不会看到空白回复。
        if not assistant_parts:
            assistant_parts.append("抱歉，我这次没能组织好回答。请换个说法再问我一次，或先告诉我你卡在哪一步。")
            yield sse_event("assistant.delta", turnId=turn_id, delta=assistant_parts[0])
        event_service.log_event(
            user_id=user_id, session_id=session_id, turn_id=turn_id,
            mode=mode, kind="model_called",
            payload={
                "provider": meta.provider,
                "model": meta.model,
                "fallback": meta.fallback,
                "fallbackReason": meta.fallback_reason,
                "requestedProvider": meta.requested_provider,
                "requestedModel": meta.requested_model,
                "thinkingTtftMs": meta.thinking_ttft_ms,
                "continuationCount": meta.continuation_count,
                "contentTtftMs": meta.content_ttft_ms,
            },
            latency_ms=meta.ttft_ms,
        )

        depth = task.desired_depth if task.desired_depth != "auto" else "L2"
        presentation = TurnPresentation(
            mode="explain",
            depth=depth,  # type: ignore[arg-type]
            focus=gate.focus,
            truncated=meta.truncated,
            plan=gate.plan,
            personalization=[
                PersonalizationItem(
                    memoryId=m.id,
                    label=m.rule[:40],
                    scope=memory_service.scope_label(m),
                    effect=effect,
                )
                for m, effect in (context.applied if context else [])
            ],
            metrics=Metrics(
                timeToFirstTokenMs=ttft_ms,
                memorySearchMs=0 if from_feedback else retrieval.search_ms,
                contextCompileMs=0 if from_feedback else context.compile_ms,
                memoryCapsuleTokens=0 if from_feedback else context.capsule_tokens,
                totalInputTokens=0 if from_feedback else context.total_input_tokens,
                # 如实上报本轮实际 provider 与降级状态：
                # 只写进事件表而客户端看不到，等于用户永远不知道自己在读模板文本。
                provider=meta.provider,
                model=meta.model,
                fallback=meta.fallback,
                fallbackReason=meta.fallback_reason,
            ),
            retrospective=_retrospective(task, gate.plan) if _knowledge_unit_closed(user_text) else None,
            outputVerification=OutputVerification(**coach_service.verify_output(task, "".join(assistant_parts))),
            suggestedActions=_suggested_actions(task.concept, task.task_scope),
        )
        _finalize_turn(
            user_id=user_id, session_id=session_id, turn_id=turn_id, mode=mode,
            brief=brief, user_text=user_text, assistant_text="".join(assistant_parts),
            focus=gate.focus, presentation=presentation, started_at=turn_started_at,
            task=task, memory_enabled=effective_memory_on,
            memory_mutations_allowed=memory_mutations_allowed,
            expected_version=_version,
            from_feedback=from_feedback,
        )
        canonical_committed = True
        yield sse_event(
            "turn.completed", turnId=turn_id, presentation=presentation.model_dump(exclude_none=True)
        )
        return
    except asyncio.CancelledError as exc:
        if not canonical_committed:
            _mark_turn_failed(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, response_text="".join(assistant_parts), exc=exc,
                error_code="CANCELLED",
            )
        raise
    except Exception as exc:  # noqa: BLE001 — 建流后异常必须以唯一 turn.error 结束
        # fail-fast 模式下 provider 失败会抛 ProviderFailure：给出可操作的原因提示，
        # 而不是笼统的 INTERNAL（"稍后重试"对密钥错误是无意义的建议）。
        error_code = "INTERNAL"
        error_message: str | None = None
        error_retryable: bool | None = None
        reason = getattr(exc, "reason", None)
        if isinstance(exc, coach_service.ProviderFailure) and isinstance(reason, str):
            error_code = "MODEL_UNAVAILABLE"
            error_message, error_retryable = provider_failure_message(reason)

        if not canonical_committed:
            _mark_turn_failed(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, response_text="".join(assistant_parts), exc=exc,
                error_code=error_code,
            )
        from ..errors import sse_error
        yield sse_error(
            error_code,
            turn_id=turn_id,
            request_id=request_id,
            message=error_message,
            retryable=error_retryable,
        )
        return


def _mark_turn_failed(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    mode: str,
    response_text: str,
    exc: BaseException,
    error_code: str,
) -> None:
    try:
        event_service.log_event(
            user_id=user_id, session_id=session_id, turn_id=turn_id,
            mode=mode, kind="turn_failed", payload={"error": type(exc).__name__},
        )
    except Exception:  # noqa: BLE001 — 失败埋点不能阻塞 Turn 收尾
        pass
    try:
        turn_store.finish_turn(
            turn_id, status="error", mode=mode,
            response_text=response_text,
            error={"code": error_code, "type": type(exc).__name__},
        )
    except Exception:  # noqa: BLE001 — 尽力收尾，保留原始异常/取消语义
        pass


def _finalize_turn(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    mode: str,
    brief,
    user_text: str,
    assistant_text: str,
    focus: str,
    presentation: TurnPresentation,
    started_at: float,
    task=None,
    clarification_question: str = "",
    memory_enabled: bool = True,
    memory_mutations_allowed: bool = True,
    expected_version: int | None = None,
    from_feedback: bool = False,
) -> None:
    """提交 P0 canonical Turn，再由调用方发送唯一 turn.completed。

    Brief/Concept/Event 是可降级的旁路；turns 表中的 canonical 状态是完成事件的硬前置。
    """
    try:
        brief_service.save_message(session_id, turn_id, "assistant", assistant_text)
        if mode == "explain" and brief.clarify_streak > 0:
            event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, kind="clarification_resolved", payload={"focus": focus},
            )
        updated = brief_service.apply_turn_delta(
            brief,
            user_text=user_text,
            focus=focus,
            mode=mode,
            task=task,
            clarification_question=clarification_question,
            from_feedback=from_feedback,
        )
        new_version, applied = brief_service.update_brief_checked(
            session_id, updated, expected_version=expected_version
        )
        if applied:
            event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, kind="session_brief_updated", payload={"focus": focus},
            )
        else:
            # CAS 冲突：本轮的 delta 未落库（并发轮次已推进版本）。
            # 不重试合并（会放大竞态），但必须留下可观测记录——
            # 否则 clarify_streak 等状态静默丢失，门控行为无法解释。
            event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, kind="brief_update_conflict",
                payload={"focus": focus, "expectedVersion": expected_version, "currentVersion": new_version},
            )
    except Exception as exc:  # noqa: BLE001
        event_service.log_event(
            user_id=user_id, session_id=session_id, turn_id=turn_id,
            mode=mode, kind="turn_failed",
            payload={"stage": "brief_update", "error": type(exc).__name__},
        )

    if not from_feedback and memory_enabled and memory_mutations_allowed and task is not None and task.concept:
        try:
            state, evidence_kind = brief_service.infer_concept_state(user_text, assistant_text)
            evt = event_service.log_event(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                mode=mode, kind="concept_state_updated",
                payload={"concept": task.concept, "state": state, "evidenceKind": evidence_kind},
            )
            brief_service.record_concept_state(
                user_id, task, state=state,
                evidence_kind=evidence_kind, source_event_id=evt,
            )
        except Exception:  # noqa: BLE001
            pass

    # canonical Turn 必须先提交成功；否则上层只发送 turn.error。
    turn_store.finish_turn(
        turn_id, status="completed", mode=mode,
        response_text=assistant_text,
        presentation=presentation.model_dump(exclude_none=True),
    )
    try:
        event_service.log_event(
            user_id=user_id, session_id=session_id, turn_id=turn_id,
            mode=mode, kind="response_completed",
            payload={"chars": len(assistant_text)},
            latency_ms=int((time.perf_counter() - started_at) * 1000),
        )
    except Exception:  # noqa: BLE001 — 指标失败不反转已提交的 canonical Turn
        pass
