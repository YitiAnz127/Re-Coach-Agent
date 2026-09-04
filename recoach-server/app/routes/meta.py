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
