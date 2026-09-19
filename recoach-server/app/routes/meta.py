from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..config import get_settings
from ..services import coach as coach_service
from ..services.compiler import POLICY_VERSION

router = APIRouter()


@router.get("/meta")
def meta():
    """返回真实服务能力；未实现的 P1/P2 能力明确为 False。"""
    settings = get_settings()
    provider, model = coach_service.resolve_provider()
    migrations = db.query("SELECT version FROM schema_migrations ORDER BY version")
    thinking_enabled = provider == "deepseek" and settings.deepseek_thinking == "enabled"
    return {
        "data": {
            "product": "知返 Re:Coach",
            "version": "1.1.0",
            "phase": "p1",
            "policyVersion": POLICY_VERSION,
            "schemaVersion": migrations[-1]["version"] if migrations else 0,
            "llm": {
                "provider": provider,
                "model": model,
                "configured": provider != "template",
                # 只暴露"某 provider 是否配了密钥"的布尔值，绝不暴露密钥内容。
                # 用途：provider 仍是 template 但密钥已填时，客户端可提示
                # "你填了密钥但没切换 provider"——这是最常见的配置失误，
                # 且不提示的话应用会一直安静地走模板。
                "keysPresent": {
                    "deepseek": bool(settings.effective_deepseek_key),
                    "anthropic": bool(settings.effective_anthropic_key),
                    "openaiCompatible": bool(settings.llm_api_key and settings.llm_base_url),
                },
                "thinkingEnabled": thinking_enabled,
                "reasoningEffort": (
                    settings.deepseek_reasoning_effort if thinking_enabled else None
                ),
            },
            "capabilities": {
                "sqlite": True,
                "fts5": db.FTS5_AVAILABLE,
                "llmProvider": provider,
                "llmModel": model,
                "memoryOn": settings.memory_on,
                "fairAbFork": True,
                "clarificationGate": True,
                "deterministicContextCompiler": True,
                "idempotentTurnRetry": True,
                "microExperiment": False,
                "complexFeedbackDistillation": False,
                "sessionRecoveryApi": False,
                "metricsSummary": True,
                "supermemorySync": False,
            },
        }
    }
