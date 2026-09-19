from __future__ import annotations

from fastapi import APIRouter, Request

from ..services import memory as memory_service
from .sessions import current_user_id_or_error

router = APIRouter()


@router.get("/memories")
def list_memories(
    request: Request,
    status: str = "active",
    type: str | None = None,
    domain: str | None = None,
):
    """只读记忆透明度（v0.6 §11.3）。修改/遗忘通过自然语言 Turn 触发，不做表单。"""
    user_id, error = current_user_id_or_error(request)
    if error is not None:
        return error
    memories = memory_service.list_memories(user_id, status=status, type_=type, domain=domain)
    return {
        "data": [
            {
                "id": m.id,
                "type": m.type,
                "rule": m.rule,
                "scope": memory_service.scope_label(m),
                "polarity": m.polarity,
                "evidenceKind": m.evidence_kind,
                "confidence": m.confidence,
                "status": m.status,
                "updatedAt": m.updated_at,
            }
            for m in memories
        ]
    }
